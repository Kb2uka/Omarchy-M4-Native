/*
 * Copyright 2026 The Asahi Linux Contributors
 * SPDX-License-Identifier: MIT
 *
 * Hardware execution is intentionally separate from `make check`.
 */
#define _GNU_SOURCE
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES3/gl3.h>
#include <GLES2/gl2ext.h>
#include <errno.h>
#include <fcntl.h>
#include <gbm.h>
#include <inttypes.h>
#include <linux/dma-buf.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

static unsigned width = 2880, height = 1800;
static const unsigned colors[4][4] = {
   {101, 517, 901, 0}, {1022, 1, 367, 1},
   {1, 1023, 643, 2}, {733, 295, 0, 3},
};

static unsigned
patch(unsigned x, unsigned y)
{
   return (x >= width / 2) + 2 * (y >= height / 2);
}

static unsigned
expected(unsigned p, unsigned c, bool masked)
{
   /* The second draw must reload G, but replace R/B using DST_ALPHA = 1. */
   return masked && c != 1 ? 1023 - colors[p][c] : colors[p][c];
}

static uint32_t
packed_rgb(uint32_t format, unsigned r, unsigned g, unsigned b)
{
   return format == GBM_FORMAT_XRGB2101010
      ? (r << 20) | (g << 10) | b : r | (g << 10) | (b << 20);
}

static bool
gl_ok(const char *operation)
{
   GLenum error = glGetError();
   if (error == GL_NO_ERROR)
      return true;
   fprintf(stderr, "%s: GL error 0x%x\n", operation, error);
   return false;
}

static GLuint
shader(GLenum stage, const char *source)
{
   GLuint obj = glCreateShader(stage);
   glShaderSource(obj, 1, &source, NULL);
   glCompileShader(obj);
   GLint ok;
   glGetShaderiv(obj, GL_COMPILE_STATUS, &ok);
   if (!ok) {
      char log[4096];
      glGetShaderInfoLog(obj, sizeof(log), NULL, log);
      fprintf(stderr, "shader: %s\n", log);
      glDeleteShader(obj);
      return 0;
   }
   return obj;
}

static GLuint
program(bool sample)
{
   const char *vs =
      "#version 300 es\n"
      "void main() {\n"
      "  vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);\n"
      "  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);\n"
      "}\n";
   const char *fs = sample
      ? "#version 300 es\n"
        "precision highp float;\n"
        "uniform highp sampler2D source;\n"
        "out vec4 color;\n"
        "void main() { color = texelFetch(source, ivec2(gl_FragCoord.xy), 0); }\n"
      : "#version 300 es\n"
        "precision highp float;\n"
        "uniform vec4 value;\n"
        "out vec4 color;\n"
        "void main() { color = value; }\n";
   GLuint v = shader(GL_VERTEX_SHADER, vs), f = shader(GL_FRAGMENT_SHADER, fs);
   if (!v || !f) {
      glDeleteShader(v);
      glDeleteShader(f);
      return 0;
   }
   GLuint obj = glCreateProgram();
   glAttachShader(obj, v);
   glAttachShader(obj, f);
   glLinkProgram(obj);
   glDeleteShader(v);
   glDeleteShader(f);
   GLint ok;
   glGetProgramiv(obj, GL_LINK_STATUS, &ok);
   if (!ok) {
      char log[4096];
      glGetProgramInfoLog(obj, sizeof(log), NULL, log);
      fprintf(stderr, "link: %s\n", log);
      glDeleteProgram(obj);
      return 0;
   }
   return obj;
}

static bool
framebuffer(GLuint texture, GLuint renderbuffer)
{
   if (renderbuffer)
      glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                                 GL_RENDERBUFFER, renderbuffer);
   else
      glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                             GL_TEXTURE_2D, texture, 0);
   GLenum status = glCheckFramebufferStatus(GL_FRAMEBUFFER);
   if (status != GL_FRAMEBUFFER_COMPLETE) {
      fprintf(stderr, "framebuffer status 0x%x\n", status);
      return false;
   }
   GLint bits[3] = {0};
   const GLenum params[] = {GL_FRAMEBUFFER_ATTACHMENT_RED_SIZE,
                            GL_FRAMEBUFFER_ATTACHMENT_GREEN_SIZE,
                            GL_FRAMEBUFFER_ATTACHMENT_BLUE_SIZE};
   for (unsigned c = 0; c < 3; ++c)
      glGetFramebufferAttachmentParameteriv(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                                            params[c], &bits[c]);
   if (bits[0] != 10 || bits[1] != 10 || bits[2] != 10) {
      fprintf(stderr, "framebuffer RGB bits=%d/%d/%d, expected 10/10/10\n",
              bits[0], bits[1], bits[2]);
      return false;
   }
   return gl_ok("framebuffer");
}

static bool
read_gl(bool masked)
{
   for (unsigned p = 0; p < 4; ++p) {
      uint32_t got = 0;
      unsigned x = (p & 1) ? width - 1 : 0;
      unsigned y = (p & 2) ? height - 1 : 0;
      glReadPixels(x, y, 1, 1, GL_RGBA, GL_UNSIGNED_INT_2_10_10_10_REV, &got);
      if (!gl_ok("10-bit glReadPixels"))
         return false;
      uint32_t want = packed_rgb(GBM_FORMAT_XBGR2101010,
         expected(p, 0, masked), expected(p, 1, masked),
         expected(p, 2, masked)) | 0xc0000000u;
      if (got != want) {
         fprintf(stderr, "GL patch=%u got=%08" PRIx32 " want=%08" PRIx32
                         " (RGBA10/10/10/2, alpha must be 3)\n", p, got, want);
         return false;
      }
   }
   return true;
}

static bool
read_memory(struct gbm_bo *bo, int fd, bool masked)
{
   /* Read the exported linear storage directly, without GBM transfer staging
    * or a format conversion. X bits are unspecified and deliberately ignored. */
   uint32_t stride = gbm_bo_get_stride(bo);
   uint32_t offset = gbm_bo_get_offset(bo, 0);
   size_t length = offset + (size_t)stride * height;
   struct dma_buf_sync sync = {.flags = DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ};
   if (ioctl(fd, DMA_BUF_IOCTL_SYNC, &sync) < 0) {
      perror("DMA_BUF_SYNC_START READ");
      return false;
   }
   void *map = mmap(NULL, length, PROT_READ, MAP_SHARED, fd, 0);
   bool ok = map != MAP_FAILED;
   if (!ok)
      perror("mmap dma-buf read-only");
   unsigned x_values = 0;
   for (unsigned y = 0; ok && y < height; ++y) {
      for (unsigned x = 0; x < width; ++x) {
         uint32_t got;
         memcpy(&got, (uint8_t *)map + offset + (size_t)y * stride + 4 * x, 4);
         unsigned p = patch(x, y);
         uint32_t want = packed_rgb(gbm_bo_get_format(bo),
            expected(p, 0, masked), expected(p, 1, masked),
            expected(p, 2, masked));
         x_values |= 1u << (got >> 30);
         if ((got & 0x3fffffffu) != want) {
            fprintf(stderr, "raw (%u,%u) got=%08" PRIx32 " want RGB=%08" PRIx32
                            "\n", x, y, got, want);
            ok = false;
            break;
         }
      }
   }
   if (map != MAP_FAILED)
      munmap(map, length);
   sync.flags = DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ;
   if (ioctl(fd, DMA_BUF_IOCTL_SYNC, &sync) < 0) {
      perror("DMA_BUF_SYNC_END READ");
      ok = false;
   }
   if (ok)
      printf("raw %ux%u RGB10 PASS; observed X values mask=0x%x (ignored)\n",
             width, height, x_values);
   return ok;
}

static void
draw(GLuint prog, bool masked)
{
   glUseProgram(prog);
   glEnable(GL_SCISSOR_TEST);
   glColorMask(GL_TRUE, masked ? GL_FALSE : GL_TRUE, GL_TRUE, GL_TRUE);
   if (masked) {
      glEnable(GL_BLEND);
      glBlendFunc(GL_DST_ALPHA, GL_ZERO);
   } else {
      glDisable(GL_BLEND);
   }
   for (unsigned p = 0; p < 4; ++p) {
      unsigned x = p & 1 ? width / 2 : 0;
      unsigned y = p & 2 ? height / 2 : 0;
      glScissor(x, y, (p & 1 ? width : width / 2) - x,
                      (p & 2 ? height : height / 2) - y);
      glUniform4f(glGetUniformLocation(prog, "value"),
         expected(p, 0, masked) / 1023.0f,
         /* Deliberately wrong G on the masked draw; old G must survive. */
         (masked ? 0 : colors[p][1]) / 1023.0f,
         expected(p, 2, masked) / 1023.0f, colors[p][3] / 3.0f);
      glDrawArrays(GL_TRIANGLES, 0, 3);
   }
   glDisable(GL_SCISSOR_TEST);
   glDisable(GL_BLEND);
   glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
   glFinish(); /* Actual completion before CPU storage inspection. */
}

static bool
run_bo(EGLDisplay display, struct gbm_bo *bo, GLuint fill, GLuint copy)
{
   PFNEGLCREATEIMAGEKHRPROC create_image =
      (PFNEGLCREATEIMAGEKHRPROC)eglGetProcAddress("eglCreateImageKHR");
   PFNEGLDESTROYIMAGEKHRPROC destroy_image =
      (PFNEGLDESTROYIMAGEKHRPROC)eglGetProcAddress("eglDestroyImageKHR");
   PFNGLEGLIMAGETARGETTEXTURE2DOESPROC image_texture =
      (PFNGLEGLIMAGETARGETTEXTURE2DOESPROC)eglGetProcAddress("glEGLImageTargetTexture2DOES");
   PFNGLEGLIMAGETARGETRENDERBUFFERSTORAGEOESPROC image_renderbuffer =
      (PFNGLEGLIMAGETARGETRENDERBUFFERSTORAGEOESPROC)
         eglGetProcAddress("glEGLImageTargetRenderbufferStorageOES");
   if (!create_image || !destroy_image || !image_texture || !image_renderbuffer) {
      fprintf(stderr, "missing EGL image entry points\n");
      return false;
   }
   uint64_t modifier = gbm_bo_get_modifier(bo);
   if (modifier != 0 || gbm_bo_get_plane_count(bo) != 1) {
      fprintf(stderr, "expected single-plane LINEAR BO, modifier=%016" PRIx64 "\n",
              modifier);
      return false;
   }
   int fd = gbm_bo_get_fd(bo);
   if (fd < 0) {
      perror("gbm_bo_get_fd");
      return false;
   }
   const EGLint attrs[] = {
      EGL_WIDTH, width, EGL_HEIGHT, height,
      EGL_LINUX_DRM_FOURCC_EXT, gbm_bo_get_format(bo),
      EGL_DMA_BUF_PLANE0_FD_EXT, fd,
      EGL_DMA_BUF_PLANE0_OFFSET_EXT, gbm_bo_get_offset(bo, 0),
      EGL_DMA_BUF_PLANE0_PITCH_EXT, gbm_bo_get_stride(bo), EGL_NONE,
   };
   EGLImageKHR image = create_image(display, EGL_NO_CONTEXT, EGL_LINUX_DMA_BUF_EXT,
                                     NULL, attrs);
   if (image == EGL_NO_IMAGE_KHR) {
      fprintf(stderr, "dma-buf image import failed: EGL 0x%x\n", eglGetError());
      close(fd);
      return false;
   }
   GLuint textures[2], fbo, renderbuffer;
   glGenTextures(2, textures);
   glGenFramebuffers(1, &fbo);
   glBindFramebuffer(GL_FRAMEBUFFER, fbo);
   glBindTexture(GL_TEXTURE_2D, textures[0]);
   image_texture(GL_TEXTURE_2D, image);
   glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
   glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
   /* ES does not allow RGB10 texture color attachments. EGL image renderbuffer
    * storage is the native window-buffer rendering path; retain the texture
    * view separately to verify X2 sampling after rendering.
    */
   glGenRenderbuffers(1, &renderbuffer);
   glBindRenderbuffer(GL_RENDERBUFFER, renderbuffer);
   image_renderbuffer(GL_RENDERBUFFER, image);
   bool ok = gl_ok("bind imported image") && framebuffer(0, renderbuffer);
   glViewport(0, 0, width, height);
   glDisable(GL_DITHER);
   for (unsigned pass = 0; ok && pass < 2; ++pass) {
      draw(fill, pass != 0);
      ok = gl_ok("fragment draw") && read_memory(bo, fd, pass != 0) &&
           read_gl(pass != 0);
      if (ok)
         printf("%s render/raw/GL readback PASS\n",
                pass ? "reload + G mask + DST_ALPHA" : "initial");
   }
   if (ok) {
      /* A separate 10-bit target proves texture order and opaque sampled
       * alpha, without framebuffer feedback or CPU pixel initialization. */
      glBindTexture(GL_TEXTURE_2D, textures[1]);
      glTexStorage2D(GL_TEXTURE_2D, 1, GL_RGB10_A2, width, height);
      ok = framebuffer(textures[1], 0);
      glActiveTexture(GL_TEXTURE0);
      glBindTexture(GL_TEXTURE_2D, textures[0]);
      glUseProgram(copy);
      glUniform1i(glGetUniformLocation(copy, "source"), 0);
      glDrawArrays(GL_TRIANGLES, 0, 3);
      glFinish();
      ok = ok && gl_ok("sample imported X2") && read_gl(true);
      if (ok)
         puts("sample RGB10 order + opaque X alpha PASS");
   }
   glBindFramebuffer(GL_FRAMEBUFFER, 0);
   glDeleteFramebuffers(1, &fbo);
   glDeleteRenderbuffers(1, &renderbuffer);
   glDeleteTextures(2, textures);
   destroy_image(display, image);
   close(fd);
   return ok;
}

static int
self_test(void)
{
   /* Golden words are independent of packed_rgb's shift implementation. */
   if (packed_rgb(GBM_FORMAT_XRGB2101010, 101, 517, 901) != 0x06581785 ||
       packed_rgb(GBM_FORMAT_XBGR2101010, 101, 517, 901) != 0x38581465 ||
       patch(0, 0) != 0 || patch(width - 1, height - 1) != 3 ||
       expected(0, 1, true) != 517 || expected(0, 0, true) != 922 ||
       expected(0, 2, true) != 122)
      return 1;
   puts("CPU self-test PASS (no DRM/GBM/EGL/GL calls)");
   return 0;
}

int
main(int argc, char **argv)
{
   if (argc == 2 && !strcmp(argv[1], "--self-test"))
      return self_test();
   if (argc != 1 && argc != 2 && argc != 4 && argc != 5) {
      fprintf(stderr, "usage: %s [render-node [width height [cycles]]] | --self-test\n", argv[0]);
      return 2;
   }
   unsigned cycles = 1;
   if (argc >= 4) {
      char *end;
      unsigned long w = strtoul(argv[2], &end, 10);
      if (*end || w < 2 || w > 16384)
         return 2;
      unsigned long h = strtoul(argv[3], &end, 10);
      if (*end || h < 2 || h > 16384)
         return 2;
      width = w;
      height = h;
   }
   if (argc == 5) {
      char *end;
      unsigned long n = strtoul(argv[4], &end, 10);
      if (*end || n < 1 || n > 64)
         return 2;
      cycles = n;
   }
   int fd = open(argc > 1 ? argv[1] : "/dev/dri/renderD128", O_RDWR | O_CLOEXEC);
   if (fd < 0) {
      perror("open render node");
      return 1;
   }
   struct gbm_device *gbm = gbm_create_device(fd);
   if (!gbm) {
      perror("gbm_create_device");
      close(fd);
      return 1;
   }
   printf("GBM backend=%s dimensions=%ux%u\n",
          gbm_device_get_backend_name(gbm), width, height);
   const uint32_t formats[] = {GBM_FORMAT_XRGB2101010, GBM_FORMAT_XBGR2101010};
   const char *names[] = {"XR30", "XB30"};
   const uint32_t usages[] = {
      GBM_BO_USE_RENDERING | GBM_BO_USE_LINEAR,
      GBM_BO_USE_RENDERING | GBM_BO_USE_LINEAR | GBM_BO_USE_SCANOUT,
   };
   struct gbm_bo *bos[2][2] = {{NULL}};
   bool ok = true;
   PFNEGLGETPLATFORMDISPLAYEXTPROC get_display =
      (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
   EGLDisplay display = get_display
      ? get_display(EGL_PLATFORM_GBM_KHR, gbm, NULL) : EGL_NO_DISPLAY;
   EGLContext context = EGL_NO_CONTEXT;
   EGLint major, minor, count;
   EGLConfig config;
   const EGLint cfg[] = {
      EGL_SURFACE_TYPE, 0,
      EGL_RENDERABLE_TYPE, EGL_OPENGL_ES3_BIT_KHR, EGL_NONE,
   };
   const EGLint ctx[] = {EGL_CONTEXT_CLIENT_VERSION, 3, EGL_NONE};
   bool initialized = display != EGL_NO_DISPLAY && eglInitialize(display, &major, &minor);
   bool current = false;
   if (initialized && eglBindAPI(EGL_OPENGL_ES_API) &&
       eglChooseConfig(display, cfg, &config, 1, &count) && count) {
      context = eglCreateContext(display, config, EGL_NO_CONTEXT, ctx);
      current = context != EGL_NO_CONTEXT &&
         eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, context);
   }
   if (!current) {
      fprintf(stderr, "EGL ES3 surfaceless initialization failed: 0x%x\n", eglGetError());
      ok = false;
   } else {
      const char *renderer = (const char *)glGetString(GL_RENDERER);
      printf("EGL=%s GL=%s renderer=%s\n", eglQueryString(display, EGL_VERSION),
             glGetString(GL_VERSION), renderer);
      FILE *maps = fopen("/proc/self/maps", "r");
      if (maps) {
         char line[4096];
         while (fgets(line, sizeof(line), maps)) {
            if (strstr(line, "/libgallium-"))
               printf("DRIVER_MAP=%s", line);
         }
         fclose(maps);
      }
      bool software = !renderer || strstr(renderer, "llvmpipe") ||
         strstr(renderer, "softpipe") || strstr(renderer, "Software Rasterizer");
      GLuint fill = program(false), copy = program(true);
      if (software || !fill || !copy) {
         fprintf(stderr, "hardware renderer and linked programs required\n");
         ok = false;
      } else {
         for (unsigned cycle = 0; cycle < cycles && ok; ++cycle) {
         printf("CYCLE %u/%u allocate-render-export-free\n", cycle + 1, cycles);
         for (unsigned f = 0; f < 2; ++f) {
            for (unsigned u = 0; u < 2; ++u) {
               int supported = gbm_device_is_format_supported(gbm, formats[f], usages[u]);
               errno = 0;
               bos[f][u] = gbm_bo_create(gbm, width, height, formats[f], usages[u]);
               int saved_errno = errno;
               printf("%s flags=%u supported=%d allocation=%s errno=%d (%s)\n",
                      names[f], usages[u], supported, bos[f][u] ? "PASS" : "FAIL",
                      saved_errno, strerror(saved_errno));
               if (!bos[f][u]) {
                  if (f == 1 && !supported)
                     printf("XB30 flags=%u SKIP: unsupported\n", usages[u]);
                  else
                     ok = false;
                  continue;
               }
               printf("validate %s flags=%u\n", names[f], usages[u]);
               bool passed = run_bo(display, bos[f][u], fill, copy);
               printf("%s flags=%u render/readback=%s\n",
                      names[f], usages[u], passed ? "PASS" : "FAIL");
               ok &= passed && supported;
               gbm_bo_destroy(bos[f][u]);
               bos[f][u] = NULL;
            }
         }
         printf("CYCLE %u/%u %s\n", cycle + 1, cycles, ok ? "PASS" : "FAIL");
         }
      }
      glDeleteProgram(fill);
      glDeleteProgram(copy);
      eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
   }
   if (context != EGL_NO_CONTEXT)
      eglDestroyContext(display, context);
   if (initialized)
      eglTerminate(display);
   for (unsigned f = 0; f < 2; ++f)
      for (unsigned u = 0; u < 2; ++u)
         if (bos[f][u])
            gbm_bo_destroy(bos[f][u]);
   gbm_device_destroy(gbm);
   close(fd);
   puts(ok ? "XR30 validation PASS" : "XR30 validation FAIL");
   return ok ? 0 : 1;
}
