obj-m += snd-soc-j713-group.o snd-soc-tas2764.o snd-soc-macaudio.o
snd-soc-j713-group-y := j713-speaker-group.o j713-audio.o
snd-soc-tas2764-y := tas2764.o
snd-soc-macaudio-y := macaudio.o
ifeq ($(J713_PROFILE),conservative-v1)
ccflags-y += -DJ713_CONSERVATIVE_PROFILE_V1
endif
obj-m += snd-soc-apple-mca.o apple-admac.o
snd-soc-apple-mca-y := mca.o
apple-admac-y := admac.o
ccflags-y += -I$(srctree)/drivers/dma
