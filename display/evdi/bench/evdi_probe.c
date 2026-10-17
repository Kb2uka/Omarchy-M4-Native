// SPDX-License-Identifier: MIT
// evdi_probe: plug a virtual 1920x1080 monitor into evdi and read back what
// the compositor draws on it, the job DisplayLinkManager does for a real
// adapter. Run as root (it adds an evdi device):
//
//   evdi_probe SECONDS OUT.ppm
//
// Prints the mode the compositor set, the frames grabbed in each second, the
// total and how many differed, and the share of non-black pixels in the last
// one, which it writes as PPM.
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include "evdi_lib.h"

#define MAX_RECTS 16

static unsigned char edid[128] = {
	0x00, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x00, // header
	0x3d, 0xa1, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, // "OMA", product 1
	0x01, 0x24, 0x01, 0x03, 0x80, 0x34, 0x1d, 0x78, // 2026, EDID 1.3, digital
	0x0a, 0xee, 0x91, 0xa3, 0x54, 0x4c, 0x99, 0x26, // preferred DTD, sRGB
	0x0f, 0x50, 0x54, 0x00, 0x00, 0x00, 0x01, 0x01,
	0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01,
	0x01, 0x01, 0x01, 0x01, 0x01, 0x01,
	// 1920x1080 at 60 Hz, 148.5 MHz (CEA-861 VIC 16 timing)
	0x02, 0x3a, 0x80, 0x18, 0x71, 0x38, 0x2d, 0x40, 0x58, 0x2c,
	0x45, 0x00, 0x0f, 0x28, 0x21, 0x00, 0x00, 0x1e,
	// monitor name
	0x00, 0x00, 0x00, 0xfc, 0x00, 'E', 'V', 'D', 'I', ' ', 'P', 'R', 'O',
	'B', 'E', '\n', ' ', ' ',
	// range limits 56-76 Hz, 30-83 kHz, 170 MHz
	0x00, 0x00, 0x00, 0xfd, 0x00, 0x38, 0x4c, 0x1e, 0x53, 0x11, 0x00,
	0x0a, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20,
	// dummy descriptor
	0x00, 0x00, 0x00, 0x10, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
	0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
	0x00, 0x00, // no extensions, checksum filled in by main()
};

struct probe {
	evdi_handle h;
	struct evdi_mode mode;
	uint32_t *pixels;
	struct evdi_rect rects[MAX_RECTS];
	bool registered;
	bool pending;
	unsigned long frames, changed;
	uint64_t last_hash;
};

static uint64_t fnv(const uint32_t *p, size_t n)
{
	uint64_t h = 1469598103934665603ULL;
	for (size_t i = 0; i < n; i++)
		h = (h ^ p[i]) * 1099511628211ULL;
	return h;
}

static void grab(struct probe *p)
{
	int n = MAX_RECTS;
	size_t count = (size_t)p->mode.width * p->mode.height;
	uint64_t h;

	evdi_grab_pixels(p->h, p->rects, &n);
	p->frames++;
	h = fnv(p->pixels, count);
	if (h != p->last_hash)
		p->changed++;
	p->last_hash = h;
}

static void request(struct probe *p)
{
	p->pending = !evdi_request_update(p->h, 1);
	if (!p->pending)
		grab(p);
}

static void on_mode(struct evdi_mode mode, void *data)
{
	struct probe *p = data;

	printf("mode %dx%d@%d bpp %d format 0x%08x\n", mode.width, mode.height,
	       mode.refresh_rate, mode.bits_per_pixel, mode.pixel_format);
	if (p->registered)
		evdi_unregister_buffer(p->h, 1);
	free(p->pixels);
	p->mode = mode;
	p->pixels = calloc((size_t)mode.width * mode.height, 4);
	if (!p->pixels) {
		perror("calloc");
		exit(1);
	}
	evdi_register_buffer(p->h, (struct evdi_buffer){
		.id = 1, .buffer = p->pixels, .width = mode.width,
		.height = mode.height, .stride = mode.width * 4,
		.rects = p->rects, .rect_count = MAX_RECTS });
	p->registered = true;
	request(p);
}

static void on_update(int buffer, void *data)
{
	struct probe *p = data;

	if (buffer == 1 && p->registered) {
		p->pending = false;
		grab(p);
	}
}

static void on_dpms(int mode, void *data)
{
	(void)data;
	printf("dpms %d\n", mode);
}

static void write_ppm(const struct probe *p, const char *path)
{
	size_t count = (size_t)p->mode.width * p->mode.height, lit = 0;
	// Root runs this: create the image owner-writable only, not 0666 & umask.
	int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
	FILE *f = fd < 0 ? NULL : fdopen(fd, "wb");

	if (!f) {
		perror(path);
		if (fd >= 0)
			close(fd);
		return;
	}
	fprintf(f, "P6\n%d %d\n255\n", p->mode.width, p->mode.height);
	for (size_t i = 0; i < count; i++) {
		uint32_t px = p->pixels[i];
		unsigned char rgb[3] = { px >> 16, px >> 8, px };

		if (px & 0xffffff)
			lit++;
		fwrite(rgb, 1, 3, f);
	}
	fclose(f);
	printf("non-black %.1f%%\n", count ? 100.0 * lit / count : 0.0);
}

int main(int argc, char **argv)
{
	struct probe p = { 0 };
	struct evdi_event_context ctx = {
		.mode_changed_handler = on_mode,
		.update_ready_handler = on_update,
		.dpms_handler = on_dpms,
		.user_data = &p,
	};
	struct timespec start, now;
	unsigned long counted = 0, changed = 0;
	unsigned char sum = 0;
	int seconds, second = 0;

	if (argc != 3) {
		fprintf(stderr, "usage: %s SECONDS OUT.ppm\n", argv[0]);
		return 2;
	}
	seconds = atoi(argv[1]);
	for (int i = 0; i < 127; i++)
		sum += edid[i];
	edid[127] = (unsigned char)(0x100 - sum);

	p.h = evdi_open_attached_to_fixed(NULL, 0);
	if (p.h == EVDI_INVALID_HANDLE) {
		fprintf(stderr, "no evdi device (is evdi loaded and are you root?)\n");
		return 1;
	}
	evdi_connect2(p.h, edid, sizeof(edid), 1920 * 1080, 1920 * 1080 * 60);
	clock_gettime(CLOCK_MONOTONIC, &start);
	for (;;) {
		struct pollfd fd = { .fd = evdi_get_event_ready(p.h), .events = POLLIN };

		clock_gettime(CLOCK_MONOTONIC, &now);
		if (now.tv_sec - start.tv_sec > second) {
			printf("second %d frames %lu changed %lu\n", second,
			       p.frames - counted, p.changed - changed);
			fflush(stdout);
			counted = p.frames;
			changed = p.changed;
			second++;
		}
		if (second >= seconds)
			break;
		// Ask for the next frame as soon as one is grabbed, and sleep only
		// while a request waits for the compositor, as a real client does.
		if (p.registered && !p.pending) {
			request(&p);
			if (!p.pending)
				continue;
		}
		if (poll(&fd, 1, 100) > 0)
			evdi_handle_events(p.h, &ctx);
	}
	printf("frames %lu changed %lu\n", p.frames, p.changed);
	if (p.pixels)
		write_ppm(&p, argv[2]);
	else
		printf("the compositor never set a mode\n");
	evdi_disconnect(p.h);
	evdi_close(p.h);
	return p.frames ? 0 : 1;
}
