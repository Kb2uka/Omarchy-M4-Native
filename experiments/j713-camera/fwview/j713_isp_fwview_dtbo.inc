#error "Generate this file on the build host before modules: dtc -I dts -O dtb -o j713-isp-fwview-overlay.dtbo j713-isp-fwview-overlay.dts && python3 embed_dtbo.py j713-isp-fwview-overlay.dtbo j713_isp_fwview_dtbo.inc"
/* The array below is not a device tree. The #error above keeps it out of a module. */
static const unsigned char j713_dm_dtbo[1] = { 0 };
static const unsigned int j713_dm_dtbo_len = 0;
