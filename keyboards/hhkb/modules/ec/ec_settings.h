#ifndef EC_SETTINGS_H
#define EC_SETTINGS_H

#include "config.h"
#include "rapid_trigger.h"
#include "ec_curve.h"
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

#ifdef HHKB_JIS
#define USER_EEPROM_MAGIC          0x484B4A45UL // JIS record geometry
#else
#define USER_EEPROM_MAGIC          0x484B4345UL // "HKCE" (versioned with CRC16)
#endif
#define USER_EEPROM_LEGACY_MAGIC   0x484B4344UL // "HKCD"
#define USER_EEPROM_C_MAGIC        0x484B4343UL // "HKCC"
#define USER_EEPROM_PREV_MAGIC     0x484B4342UL // "HKCB"
#define USER_EEPROM_OLD_MAGIC      0x484B4341UL // "HKCA"
#define USER_EEPROM_ADDR           0x08081500UL
#define USER_EEPROM_VERSION        2

typedef struct __attribute__((aligned(4))) {
    uint32_t magic;
    uint8_t  version;
    uint8_t  actuation_pct;
    uint16_t crc16;
    uint16_t baseline[MATRIX_COLS][MATRIX_ROWS]; // 120 bytes
    uint16_t peak[MATRIX_COLS][MATRIX_ROWS];     // 120 bytes
    rt_cfg_t rt;                                 // 10 bytes
    uint8_t  scan_rate;                          // 0..4
    uint8_t  tapping_term_10ms;                  // 5..50
    uint8_t  quick_tap_10ms;                     // 0..50
    uint8_t  flags;                              // bit 0: permissive_hold, bit 1: tap_pref, bit 2: oversampling, bit 3: custom_cal, bit 4: debug, bit 5: system, bit 6..7: led0
    uint16_t classic_release_pm;
    uint8_t curve[EC_CURVE_FIELDS];
    uint8_t led_mode[2]; // Hybrid: led_mode[0] high nibble stores sleep Hz (0 = legacy 1 Hz)
    uint8_t debug_flags; // bits 0..3: debug; bit 7: packed Hybrid deep-timer format
    uint8_t reserved[3];
} hhkb_eeprom_cal_t;
_Static_assert(offsetof(hhkb_eeprom_cal_t, curve) == 24 + 4 * MATRIX_ROWS * MATRIX_COLS, "Preserve the v1 settings record");

/*
 * Per-key Rapid Trigger profiles and actuation points.
 * A separate record keeps the main record and its migrations unchanged.
 * It uses a fixed 15x5 geometry for ANSI and JIS, and is written only once
 * a per-key setting is used, so stock EEPROM stays untouched until then.
 *
 * Each key has a Rapid Trigger profile 0..3, split over two row-mask planes
 * (rt_rows = bit 0, rt_rows_hi = bit 1). Profile 1 uses the shared RT press and
 * release sensitivity; profiles 2 and 3 have their own. Profile 0 means no
 * Rapid Trigger in "Selected keys" mode and profile 1 in "All keys" mode.
 * Version 1 records (100 bytes, one plane, no profiles) load as profile 1.
 */
#ifdef HHKB_JIS
#define HHKB_KEYCFG_MAGIC   0x4A504B48UL // "HKPJ"
#else
#define HHKB_KEYCFG_MAGIC   0x4B504B48UL // "HKPK"
#endif
#define HHKB_KEYCFG_ADDR    0x08081700UL
#define HHKB_KEYCFG_VERSION 2
#define HHKB_KEYCFG_V1_SIZE 100
#define HHKB_KEYCFG_COLS    15
#define HHKB_KEYCFG_ROWS    5
#define HHKB_KEYCFG_FLAG_RT_SELECTED 0x01U
#define HHKB_KEY_ACT_MAX    45 // per-key actuation steps of 25 permille (0.1 mm)
#define HHKB_RT_PROFILES    3  // profile 1 = shared sensitivity, 2..3 = own sensitivity
#define HHKB_RT_STEPS_MAX   45 // RT sensitivity steps of 25 permille (0.1 mm)
#define HHKB_RT_BOTTOM_DZ_MAX     10 // RT bottom dead zone steps of 25 permille (0.0..1.0 mm)
#define HHKB_RT_BOTTOM_DZ_DEFAULT 5  // 0.5 mm

typedef struct __attribute__((aligned(4))) {
    uint32_t magic;
    uint8_t  version;
    uint8_t  flags;                                   // bit 0: Rapid Trigger only on selected keys
    uint16_t crc16;                                   // covers rt_rows..reserved
    uint8_t  rt_rows[HHKB_KEYCFG_COLS];               // bit r: profile bit 0 for (col, r)
    uint8_t  act[HHKB_KEYCFG_COLS][HHKB_KEYCFG_ROWS]; // 0 = shared point, 1..45 = steps of 25 permille
    // Version 2. In version 1 the first two bytes were reserved (zero).
    uint8_t  rt_profile[HHKB_RT_PROFILES - 1][2];     // profiles 2..3: press, release steps (1..45)
    uint8_t  rt_rows_hi[HHKB_KEYCFG_COLS];            // bit r: profile bit 1 for (col, r)
    uint8_t  rt_bottom_dz;                            // RT bottom dead zone steps + 1; 0 = default
    uint8_t  reserved[2];
} hhkb_eeprom_keycfg_t;
_Static_assert(sizeof(hhkb_eeprom_keycfg_t) == 120, "Per-key record layout changed");
_Static_assert(offsetof(hhkb_eeprom_keycfg_t, rt_profile) == HHKB_KEYCFG_V1_SIZE - 2, "Keep the version 1 layout");
_Static_assert(MATRIX_COLS <= HHKB_KEYCFG_COLS && MATRIX_ROWS <= HHKB_KEYCFG_ROWS, "Matrix exceeds per-key record");
_Static_assert(MATRIX_ROWS <= 8, "Rapid Trigger row masks are 8 bits wide");
_Static_assert(USER_EEPROM_ADDR + sizeof(hhkb_eeprom_cal_t) <= HHKB_KEYCFG_ADDR, "Per-key record overlaps settings");
_Static_assert(HHKB_KEYCFG_ADDR + sizeof(hhkb_eeprom_keycfg_t) <= 0x08081800UL, "Per-key record exceeds EEPROM");

// Rapid Trigger mode reported and accepted by VIA id 3.
enum {
    HHKB_RT_MODE_OFF      = 0,
    HHKB_RT_MODE_ALL      = 1,
    HHKB_RT_MODE_SELECTED = 2,
};

// Calibration & Settings API
void hhkb_settings_init(void);
void hhkb_calibration_load(void);
void hhkb_calibration_save_to_eeprom(void);
void hhkb_calibration_start(void);
void hhkb_calibration_finish(void);
bool hhkb_calibration_enabled(void);
void hhkb_calibration_set_enabled(bool enabled);
bool hhkb_is_calibrating(void);
bool hhkb_settings_storage_writable(void);
void hhkb_settings_mark_dirty(void);
void vial_keyboard_eeprom_task(void);

#ifdef HHKB_HYBRID
uint8_t hhkb_bt_sleep_minutes(void);
uint8_t hhkb_bt_sleep_hz(void);
bool hhkb_bt_set_sleep_hz(uint8_t hz);
bool hhkb_bt_matrix_wake(void);
void hhkb_bt_set_sleep(uint8_t minutes, bool matrix_wake);
uint8_t hhkb_bt_indicator_flags(void);
void hhkb_bt_set_indicator_flag(uint8_t flag, bool enabled);
uint8_t hhkb_bt_button_action(bool long_press);
void hhkb_bt_set_button_action(bool long_press, uint8_t action);
uint8_t hhkb_bt_last_slot(void);
void hhkb_bt_set_last_slot(uint8_t slot);
#endif

// Getters & Setters for VIA
uint8_t hhkb_get_scan_rate(void);
uint8_t hhkb_get_effective_scan_rate(void);
void hhkb_set_scan_rate(uint8_t rate);
bool hhkb_get_power_saving(void);
void hhkb_set_power_saving(bool enabled);
bool hhkb_get_oversampling(void);
void hhkb_set_oversampling(bool enabled);
uint8_t hhkb_get_hold_tap_setting(uint8_t id);
void hhkb_set_hold_tap_setting(uint8_t id, uint8_t value);
uint16_t hold_tap_config_value(uint8_t id);
uint8_t hhkb_get_led_mode(uint8_t index);
void hhkb_set_led_mode(uint8_t index, uint8_t mode);
uint8_t hhkb_get_system(void);
void hhkb_set_system(uint8_t system);
uint8_t hhkb_rt_get(uint8_t id);
void hhkb_rt_set(uint8_t id, uint8_t value);
void hhkb_set_actuation_point_pct(uint8_t pct);
uint8_t hhkb_get_actuation_point_pct(void);

// Per-key settings, addressed by matrix position.
uint8_t hhkb_get_key_rt_profile(uint8_t col, uint8_t row);                  // 0..HHKB_RT_PROFILES
bool hhkb_set_key_rt_profile(uint8_t col, uint8_t row, uint8_t profile);
uint8_t hhkb_get_rt_profile_steps(uint8_t profile, bool release);           // profiles 2..3
bool hhkb_set_rt_profile_steps(uint8_t profile, bool release, uint8_t steps);
uint8_t hhkb_get_rt_bottom_dz(void);                                        // 0..HHKB_RT_BOTTOM_DZ_MAX
bool hhkb_set_rt_bottom_dz(uint8_t steps);
uint8_t hhkb_get_key_act(uint8_t col, uint8_t row);
bool hhkb_set_key_act(uint8_t col, uint8_t row, uint8_t steps);
uint16_t hhkb_get_key_act_pm(uint8_t col, uint8_t row); // effective permille, 0 = shared point
uint32_t hhkb_key_cfg_revision(void);
bool hhkb_keycfg_storage_writable(void);
void hhkb_get_key_cfg_status_str(char *buf, size_t max_len);

// Debug Console & Monitoring API
enum {
    DEBUG_FLAG_EC    = 1U << 0, // 0x01: EC matrix debug [EC]
    DEBUG_FLAG_TOPRE = 1U << 0, // Backward-compatible alias
    DEBUG_FLAG_VIA   = 1U << 1, // 0x02: VIA telemetry diag [VIA_DIAG]
    DEBUG_FLAG_RATE  = 1U << 2, // 0x04: Polling/scan rate monitor [RATE]
    DEBUG_FLAG_CAL   = 1U << 3, // 0x08: Calibration progress monitor [CAL]
};

bool hhkb_get_debug_flag(uint8_t flag);
void hhkb_set_debug_flag(uint8_t flag, bool enabled);
void hhkb_record_matrix_scan(void);
uint32_t hhkb_get_matrix_rate_hz(void);
void hhkb_calc_rates(uint32_t elapsed_ms);

bool hhkb_get_debug_enabled(void);
void hhkb_set_debug_enabled(bool enabled);
void hhkb_debug_feed_sample(uint8_t col, uint8_t row, uint16_t raw, uint16_t norm, bool is_pressed);
void hhkb_get_debug_key_str(char *buf, size_t max_len);
void hhkb_get_debug_raw_str(char *buf, size_t max_len);
void hhkb_get_debug_travel_str(char *buf, size_t max_len);
void hhkb_get_cal_status_str(char *buf, size_t max_len);

// Direct matrix arrays for zero-call-overhead inner loop scan
extern uint16_t s_cal_peak[MATRIX_COLS][MATRIX_ROWS];
extern rt_key_t s_rt_keys[MATRIX_COLS][MATRIX_ROWS];

// Matrix accessors for scan module
// Fills cfgs[0] (classic) and cfgs[1..3] (Rapid Trigger profiles), and per column the two
// row-mask planes of the effective profile index (mode and "All keys" default applied).
void hhkb_get_scan_cfgs(rt_cfg_t cfgs[HHKB_RT_PROFILES + 1], uint8_t rt_lo[MATRIX_COLS],
                        uint8_t rt_hi[MATRIX_COLS]);
uint16_t hhkb_get_cal_baseline(uint8_t col, uint8_t row);
uint16_t hhkb_get_cal_peak(uint8_t col, uint8_t row);
void hhkb_cal_accumulate_baseline(uint8_t col, uint8_t row, uint16_t val);
void hhkb_cal_record_peak(uint8_t col, uint8_t row, uint16_t val);
bool hhkb_cal_is_capturing_baseline(void);
void hhkb_cal_decrement_samples(void);
rt_key_t *hhkb_get_rt_key(uint8_t col, uint8_t row);

#ifdef __cplusplus
}
#endif

#endif // EC_SETTINGS_H

bool hhkb_bt_oversampling(void);
void hhkb_bt_set_oversampling(bool enabled);

uint8_t hhkb_bt_deep_minutes(void);
bool hhkb_bt_set_deep_minutes(uint8_t minutes);
