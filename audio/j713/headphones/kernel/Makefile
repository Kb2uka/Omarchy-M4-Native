# Build against the exact prepared tree (see ../README.md, "Build"):
#   make -C <kernel> ARCH=arm64 LLVM=1 M=<this dir> modules
# kbuild compiles j713-headphones.dtso and wraps the blob into j713_hp_dt.ko
# (symbols __dtbo_j713_headphones_begin/_end), so source and blob cannot drift.
obj-m += j713_hp_dt.o snd-soc-j713-headphones.o
j713_hp_dt-y := j713_hp_dt_loader.o j713-headphones.dtbo.o
snd-soc-j713-headphones-y := j713_headphones.o
# The 0xfeed00NN placeholder cells are not phandles, and the pmgr node's
# address cells come from the live parent: expected dtc warnings.
DTC_FLAGS_j713-headphones := -Wno-reg_format -Wno-avoid_default_addr_size \
	-Wno-dmas_property -Wno-interrupts_extended_property \
	-Wno-power_domains_property -Wno-gpios_property
