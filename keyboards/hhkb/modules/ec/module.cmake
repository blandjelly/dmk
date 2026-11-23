target_sources(${TARGET_NAME} PRIVATE
    "${DMK_ROOT}/dmk_core/features/ec_curve.c"
    "${CMAKE_CURRENT_LIST_DIR}/classic_matrix.c"
    "${CMAKE_CURRENT_LIST_DIR}/ec_settings.c"
    "${CMAKE_CURRENT_LIST_DIR}/matrix.c"
    "${CMAKE_CURRENT_LIST_DIR}/rapid_trigger.c"
)
# One data section per file lets GCC address all of ec_settings.c's statics from one anchor
# instead of a literal-pool entry per variable: about 270 bytes less flash on the 64 KiB Bank 2.
# Every static in the file is used, so --gc-sections has nothing to drop there anyway.
set_source_files_properties("${CMAKE_CURRENT_LIST_DIR}/ec_settings.c" PROPERTIES COMPILE_OPTIONS "-fno-data-sections")
