if(HHKB_BUILD_HYBRID)
    target_sources(${TARGET_NAME} PRIVATE
        "${CMAKE_CURRENT_LIST_DIR}/hybrid.c"
        "${CMAKE_CURRENT_LIST_DIR}/hybrid_stop.c")
    target_include_directories(${TARGET_NAME} PRIVATE "${CMAKE_CURRENT_LIST_DIR}")
endif()
# As for ec_settings.c: one data section lets GCC address hybrid.c's statics from one anchor,
# about 200 bytes less flash. Every static in the file is used, so none is kept needlessly.
set_source_files_properties("${CMAKE_CURRENT_LIST_DIR}/hybrid.c" PROPERTIES COMPILE_OPTIONS "-fno-data-sections")
