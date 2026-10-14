# Host contract test of ../j713_hp_plan.h (no kernel, no hardware).
CC ?= cc
CFLAGS ?= -std=gnu11 -O2 -Wall -Wextra -Werror

check: plan_test
	./plan_test

plan_test: plan_test.c ../j713_hp_plan.h
	$(CC) $(CFLAGS) -o $@ plan_test.c

clean:
	rm -f plan_test

.PHONY: check clean
