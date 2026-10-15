# Host contract test of ../j713_hp_plan.h (no kernel, no hardware). It reads
# the speaker's canonical builders in audio/j713/kernel/m4_t8132_rx.h as an
# oracle; the module build never sees this directory.
CC ?= cc
CFLAGS ?= -std=gnu11 -O2 -Wall -Wextra -Werror
INCLUDES := -I.. -I../../../kernel

check: plan_test
	./plan_test

plan_test: plan_test.c ../j713_hp_plan.h ../../../kernel/m4_t8132_rx.h
	$(CC) $(CFLAGS) $(INCLUDES) -o $@ plan_test.c

clean:
	rm -f plan_test

.PHONY: check clean
