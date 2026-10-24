CC ?= cc
CPPFLAGS += $(shell pkg-config --cflags gbm egl glesv2)
CFLAGS ?= -O2 -g
CFLAGS += -std=c11 -Wall -Wextra -Werror
LDLIBS += $(shell pkg-config --libs gbm egl glesv2)

.PHONY: all check clean
all: xr30-probe

xr30-probe: xr30-probe.c
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ $< $(LDLIBS)

# This mode returns before opening a DRM node or calling GBM/EGL/GL.
check: xr30-probe
	./xr30-probe --self-test

clean:
	rm -f xr30-probe
