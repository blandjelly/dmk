"""Round-trip the production EEPROM save/load paths for ANSI and JIS."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from .test_hybrid_button import ROOT, function


class HybridSleepSettingsTest(unittest.TestCase):
    def test_eeprom_round_trip_and_legacy_default(self):
        source = (ROOT / 'keyboards/hhkb/modules/ec/ec_settings.c').read_text()
        header = (ROOT / 'keyboards/hhkb/modules/ec/ec_settings.h').read_text()
        record = header[header.index('typedef struct'):header.index('_Static_assert')]
        constants = header[header.index('#ifdef HHKB_JIS'):header.index('typedef struct')]
        constants = re.sub(r'#define\s+USER_EEPROM_ADDR\s+\S+', '#define USER_EEPROM_ADDR ((uintptr_t)storage)', constants)
        # Per-key record constants and layout; it lives after the main record in the mock storage.
        keycfg_start = header.index('#ifdef HHKB_JIS', header.index('_Static_assert'))
        keycfg = header[keycfg_start:header.index('_Static_assert(sizeof(hhkb_eeprom_keycfg_t)')]
        keycfg = re.sub(r'#define\s+HHKB_KEYCFG_ADDR\s+\S+', '#define HHKB_KEYCFG_ADDR ((uintptr_t)storage + 0x200)', keycfg)
        write_errors = source[source.index('#define EEPROM_WRITE_ERRORS'):source.index('// Writes only the words that differ')]
        decl = source[source.index('static rt_cfg_t s_rt_cfg'):source.index('static volatile bool s_settings_dirty')]
        code = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include "rapid_trigger.h"
#include "ec_curve.h"
#include "settings_storage_guard.h"
#define WORD_ALIGNED_SIZE(x) (((x)+3U)&~3U)
#define MATRIX_COLS 15
#define DEBUG_FLAG_TOPRE 1
#define pdMS_TO_TICKS(x) (x)
#define FLASH_PECR_PELOCK 1
#define FLASH_SR_BSY 1
#define FLASH_SR_WRPERR 0
#define FLASH_SR_PGAERR 0
#define FLASH_SR_SIZERR 0
#define FLASH_SR_NOTZEROERR 0
#define FLASH_SR_FWWERR 0
#define FLASH_SR_EOP 0
static struct {uint32_t PECR,PEKEYR,SR;} flash;
#define FLASH (&flash)
static uint32_t storage[256];
static uint16_t s_cal_baseline[MATRIX_COLS][MATRIX_ROWS],s_cal_peak[MATRIX_COLS][MATRIX_ROWS];
static rt_key_t s_rt_keys[MATRIX_COLS][MATRIX_ROWS];
static bool s_custom_calibration=true;
static uint8_t s_debug_flags;
uint8_t ec_curve_settings[EC_CURVE_FIELDS];
static unsigned dirty;
static bool s_settings_dirty;
static bool hhkb_eeprom_clear_words(volatile uint32_t *dst,size_t n){while(n--)dst[n]=0;return true;}
static void hhkb_settings_mark_dirty(void){++dirty;}
bool hhkb_settings_storage_writable(void);
static void vTaskDelay(unsigned t){(void)t;}
#define taskENTER_CRITICAL() do{}while(0)
#define taskEXIT_CRITICAL() do{}while(0)
static uint16_t get_default_baseline(unsigned c,unsigned r){(void)c;(void)r;return 300;}
static uint16_t get_default_peak(unsigned c,unsigned r){(void)c;(void)r;return 2800;}
static void rt_cfg_defaults(rt_cfg_t *c){memset(c,0,sizeof(*c));}
static void rt_key_set_range(rt_key_t *k,uint16_t b,uint16_t p){(void)k;(void)b;(void)p;}
static void rt_key_reset(rt_key_t *k){(void)k;}
bool ec_curve_set(uint8_t id,uint8_t value){(void)id;(void)value;return true;}
void ec_curve_rebuild(void){}
'''
        # Header prototypes for the mocked RT functions are external.
        code = code.replace('static void rt_', 'void rt_')
        code += constants + record + keycfg + write_errors + decl
        for signature in ('static size_t keycfg_owned_size(', 'bool vial_keyboard_settings_reset(', 'bool hhkb_settings_storage_writable(', 'static uint16_t compute_crc16(', 'uint8_t hhkb_bt_sleep_hz(',
                          'bool hhkb_bt_set_sleep_hz(', 'bool hhkb_keycfg_storage_writable(',
                          'static bool eeprom_write_record(', 'static bool save_settings_record(',
                          'static bool keycfg_has_settings(', 'static bool keycfg_save_needed(',
                          'static bool save_keycfg_record(', 'static void keycfg_load(',
                          'void hhkb_calibration_save_to_eeprom(', 'static void load_rt_cfg(',
                          'static void load_cal_ranges(', 'void hhkb_calibration_load(',
                          'static inline bool key_in_matrix(', 'uint8_t hhkb_get_key_rt_profile(',
                          'bool hhkb_set_key_rt_profile(', 'void hhkb_get_scan_cfgs(',
                          'uint8_t hhkb_get_rt_bottom_dz(', 'bool hhkb_set_rt_bottom_dz('):
            code += function(source, signature) + '\n'
        code += r'''
int main(void){
 for(unsigned hz=1;hz<=10;hz++){
  assert(hhkb_bt_set_sleep_hz(hz));
  for(unsigned led=0;led<=4;led++)for(unsigned enabled=0;enabled<=1;enabled++){
   s_led_mode[0]=led;s_led_mode[1]=4;
   s_bt_matrix_wake=enabled;s_bt_sleep_minutes=23;s_bt_deep_index=7;
   s_bt_short_action=5;s_bt_long_action=2;s_bt_last_slot=3;
   for(unsigned c=0;c<MATRIX_COLS;c++)for(unsigned r=0;r<MATRIX_ROWS;r++){
    s_cal_baseline[c][r]=300+c;s_cal_peak[c][r]=2800+r;
   }
   assert(hhkb_settings_storage_writable());
   flash.PECR=0;hhkb_calibration_save_to_eeprom();
   assert(hhkb_settings_storage_writable());
   s_bt_sleep_hz=1;s_bt_matrix_wake=false;s_bt_sleep_minutes=0;
   s_bt_deep_index=0;s_bt_short_action=0;s_bt_long_action=0;s_bt_last_slot=0;
   s_led_mode[0]=0;s_led_mode[1]=0;
   hhkb_calibration_load();
   assert(hhkb_bt_sleep_hz()==hz && s_bt_matrix_wake==enabled && s_bt_sleep_minutes==23);
   assert(s_bt_deep_index==7 && s_bt_short_action==5 && s_bt_long_action==2 && s_bt_last_slot==3);
   assert(s_led_mode[0]==led && s_led_mode[1]==4);
   for(unsigned c=0;c<MATRIX_COLS;c++)for(unsigned r=0;r<MATRIX_ROWS;r++){
    assert(s_cal_baseline[c][r]==300+c && s_cal_peak[c][r]==2800+r);
   }
  }
 }
 assert(!hhkb_bt_set_sleep_hz(0) && !hhkb_bt_set_sleep_hz(11) && !hhkb_bt_set_sleep_hz(255));
 hhkb_eeprom_cal_t *cal=(hhkb_eeprom_cal_t *)storage;
 // Old record: no rate stored. Recalculate its real CRC before loading.
 cal->led_mode[0]&=15;
 cal->crc16=compute_crc16((const uint8_t *)&cal->baseline,sizeof(*cal)-offsetof(hhkb_eeprom_cal_t,baseline));
 s_bt_sleep_hz=1;hhkb_calibration_load();assert(hhkb_bt_sleep_hz()==1 && s_led_mode[0]==4);
 // Reject ANSI legacy data instead of reading it as a five-row record.
#ifdef HHKB_JIS
 cal->magic=USER_EEPROM_LEGACY_MAGIC;
 hhkb_calibration_load();
 for(unsigned c=0;c<MATRIX_COLS;c++)for(unsigned r=0;r<MATRIX_ROWS;r++)
  assert(s_cal_baseline[c][r]==300 && s_cal_peak[c][r]==2800);
 cal->magic=USER_EEPROM_MAGIC;
#endif
 // Corrupted data must not enable matrix wake or load the new rate.
 cal->led_mode[0]^=0x80;s_bt_sleep_hz=1;s_bt_matrix_wake=false;
 hhkb_calibration_load();assert(hhkb_bt_sleep_hz()==1 && !s_bt_matrix_wake);
 // Per-key record: untouched until used, then round-trips independently of the main record.
 uint8_t *keys=(uint8_t *)storage+0x200;
 for(unsigned i=0;i<sizeof(hhkb_eeprom_keycfg_t);i++)assert(keys[i]==0);
 s_rt_selected_only=true;s_rt_key_rows[3]=0x5;s_key_act[2][1]=12;s_key_act[14][MATRIX_ROWS-1]=45;
 flash.PECR=0;hhkb_calibration_save_to_eeprom();
 assert(((hhkb_eeprom_keycfg_t *)keys)->magic==HHKB_KEYCFG_MAGIC);
 s_rt_selected_only=false;memset(s_rt_key_rows,0,sizeof(s_rt_key_rows));memset(s_key_act,0,sizeof(s_key_act));
 hhkb_calibration_load();
 assert(s_rt_selected_only && s_rt_key_rows[3]==0x5 && s_key_act[2][1]==12 && s_key_act[14][MATRIX_ROWS-1]==45);
 // A corrupted per-key record falls back to shared settings.
 keys[20]^=1;hhkb_calibration_load();
 assert(!s_rt_selected_only && s_rt_key_rows[3]==0 && s_key_act[2][1]==0);
 keys[20]^=1;
 // Rapid Trigger profiles: second row-mask plane and profile sensitivities round-trip.
 hhkb_eeprom_keycfg_t *kc=(hhkb_eeprom_keycfg_t *)keys;
 // The failed load above restored defaults in RAM; set the values to save again.
 s_rt_selected_only=true;s_rt_key_rows[3]=0x5;s_key_act[2][1]=12;
 s_rt_key_rows_hi[7]=0x3;s_rt_profile[0][0]=1;s_rt_profile[0][1]=3;s_rt_profile[1][0]=45;s_rt_profile[1][1]=9;
 flash.PECR=0;hhkb_calibration_save_to_eeprom();
 assert(kc->version==HHKB_KEYCFG_VERSION);
 memset(s_rt_key_rows_hi,0,sizeof(s_rt_key_rows_hi));memset(s_rt_profile,0,sizeof(s_rt_profile));
 hhkb_calibration_load();
 assert(s_rt_key_rows_hi[7]==0x3 && s_rt_key_rows[3]==0x5);
 assert(s_rt_profile[0][0]==1 && s_rt_profile[0][1]==3 && s_rt_profile[1][0]==45 && s_rt_profile[1][1]==9);
 // Out-of-range profile steps load as defaults.
 kc->rt_profile[1][0]=46;
 kc->crc16=compute_crc16(kc->rt_rows,sizeof(*kc)-offsetof(hhkb_eeprom_keycfg_t,rt_rows));
 hhkb_calibration_load();assert(s_rt_profile[1][0]==5 && s_rt_profile[1][1]==9);
 // Rapid Trigger bottom dead zone: 0.5 mm by default, 0..1.0 mm round-trips.
 assert(hhkb_get_rt_bottom_dz()==HHKB_RT_BOTTOM_DZ_DEFAULT && kc->rt_bottom_dz==HHKB_RT_BOTTOM_DZ_DEFAULT+1);
 assert(!hhkb_set_rt_bottom_dz(HHKB_RT_BOTTOM_DZ_MAX+1) && hhkb_get_rt_bottom_dz()==HHKB_RT_BOTTOM_DZ_DEFAULT);
 for(unsigned dz=0;dz<=HHKB_RT_BOTTOM_DZ_MAX;dz++){
  assert(hhkb_set_rt_bottom_dz(dz));
  flash.PECR=0;hhkb_calibration_save_to_eeprom();
  s_rt_bottom_dz=HHKB_RT_BOTTOM_DZ_DEFAULT;hhkb_calibration_load();
  assert(hhkb_get_rt_bottom_dz()==dz && s_rt_profile[1][1]==9);
 }
 // Records written before the field existed (zero) and out-of-range values load the default.
 for(unsigned stored=0;stored<=HHKB_RT_BOTTOM_DZ_MAX+2;stored+=HHKB_RT_BOTTOM_DZ_MAX+2){
  kc->rt_bottom_dz=stored;
  kc->crc16=compute_crc16(kc->rt_rows,sizeof(*kc)-offsetof(hhkb_eeprom_keycfg_t,rt_rows));
  s_rt_bottom_dz=0;hhkb_calibration_load();assert(hhkb_get_rt_bottom_dz()==HHKB_RT_BOTTOM_DZ_DEFAULT);
 }
 // A non-default zone alone is enough to create the per-key record.
 s_rt_selected_only=false;memset(s_rt_key_rows,0,sizeof(s_rt_key_rows));memset(s_rt_key_rows_hi,0,sizeof(s_rt_key_rows_hi));
 memset(s_key_act,0,sizeof(s_key_act));memcpy(s_rt_profile,s_rt_profile_defaults,sizeof(s_rt_profile));
 assert(!keycfg_has_settings());s_rt_bottom_dz=3;assert(keycfg_has_settings());
 // Restore the record the following checks expect.
 s_rt_bottom_dz=HHKB_RT_BOTTOM_DZ_DEFAULT;
 s_rt_selected_only=true;s_rt_key_rows[3]=0x5;s_key_act[2][1]=12;
 s_rt_key_rows_hi[7]=0x3;s_rt_profile[0][0]=1;s_rt_profile[0][1]=3;s_rt_profile[1][0]=5;s_rt_profile[1][1]=9;
 flash.PECR=0;hhkb_calibration_save_to_eeprom();
 // A version 1 record (100 bytes, one plane) loads its keys as profile 1 with default profiles.
 memset(keys+HHKB_KEYCFG_V1_SIZE-2,0,sizeof(*kc)-(HHKB_KEYCFG_V1_SIZE-2));
 kc->version=1;
 kc->crc16=compute_crc16(kc->rt_rows,HHKB_KEYCFG_V1_SIZE-offsetof(hhkb_eeprom_keycfg_t,rt_rows));
 hhkb_calibration_load();
 assert(s_rt_selected_only && s_rt_key_rows[3]==0x5 && s_rt_key_rows_hi[7]==0 && s_key_act[2][1]==12);
 assert(s_rt_profile[0][0]==2 && s_rt_profile[1][1]==5);
 // Upgrading a version 1 record needs the bytes it did not own to be empty.
 keys[110]=0xAA;assert(!hhkb_keycfg_storage_writable());
 keys[110]=0;assert(hhkb_keycfg_storage_writable());
 flash.PECR=0;hhkb_calibration_save_to_eeprom();assert(kc->version==HHKB_KEYCFG_VERSION);
 // Scan mapping: Off = classic everywhere, All = Default becomes profile 1, Selected = as stored.
 memset(s_rt_key_rows,0,sizeof(s_rt_key_rows));memset(s_rt_key_rows_hi,0,sizeof(s_rt_key_rows_hi));
 assert(hhkb_set_key_rt_profile(1,0,2) && hhkb_set_key_rt_profile(2,1,3) && hhkb_set_key_rt_profile(3,2,1));
 assert(!hhkb_set_key_rt_profile(1,0,4) && !hhkb_set_key_rt_profile(1,MATRIX_ROWS,1));
 assert(hhkb_get_key_rt_profile(1,0)==2 && hhkb_get_key_rt_profile(2,1)==3 && hhkb_get_key_rt_profile(3,2)==1);
 rt_cfg_t cfgs[HHKB_RT_PROFILES+1];uint8_t lo[MATRIX_COLS],hi[MATRIX_COLS];
#define PROFILE(c,r) ((((lo[c])>>(r))&1)|((((hi[c])>>(r))&1)<<1))
 s_rt_cfg.rt_enabled=false;hhkb_get_scan_cfgs(cfgs,lo,hi);
 assert(PROFILE(1,0)==0 && PROFILE(2,1)==0 && PROFILE(4,0)==0);
 s_rt_cfg.rt_enabled=true;s_rt_selected_only=true;hhkb_get_scan_cfgs(cfgs,lo,hi);
 assert(PROFILE(1,0)==2 && PROFILE(2,1)==3 && PROFILE(3,2)==1 && PROFILE(4,0)==0);
 s_rt_selected_only=false;hhkb_get_scan_cfgs(cfgs,lo,hi);
 assert(PROFILE(1,0)==2 && PROFILE(2,1)==3 && PROFILE(3,2)==1 && PROFILE(4,0)==1);
 s_rt_cfg.press_pm=50;s_rt_cfg.release_pm=75;s_classic_release_pm=100;
 s_rt_profile[0][0]=1;s_rt_profile[0][1]=3;s_rt_profile[1][0]=7;s_rt_profile[1][1]=8;
 hhkb_get_scan_cfgs(cfgs,lo,hi);
 assert(!cfgs[0].rt_enabled && cfgs[0].release_pm==100);
 assert(cfgs[1].rt_enabled && cfgs[1].press_pm==50 && cfgs[1].release_pm==75);
 assert(cfgs[2].rt_enabled && cfgs[2].press_pm==25 && cfgs[2].release_pm==75);
 assert(cfgs[3].rt_enabled && cfgs[3].press_pm==175 && cfgs[3].release_pm==200);
 s_rt_bottom_dz=7;hhkb_get_scan_cfgs(cfgs,lo,hi);
 for(unsigned p=0;p<=HHKB_RT_PROFILES;p++)assert(cfgs[p].bottom_dz==7);
 assert(s_rt_cfg.bottom_dz==0); // the main record's padding byte stays zero
 s_rt_bottom_dz=HHKB_RT_BOTTOM_DZ_DEFAULT;
 assert(vial_keyboard_settings_reset());
 for(unsigned i=0;i<sizeof(*cal);i++)assert(((uint8_t *)cal)[i]==0);
 for(unsigned i=0;i<sizeof(hhkb_eeprom_keycfg_t);i++)assert(keys[i]==0);
 assert(hhkb_settings_storage_writable());
 // Foreign bytes in the per-key area are never overwritten or erased.
 keys[50]=0xAA;s_key_act[2][1]=12;assert(!hhkb_keycfg_storage_writable());
 flash.PECR=0;hhkb_calibration_save_to_eeprom();assert(keys[0]==0 && keys[50]==0xAA);
 assert(vial_keyboard_settings_reset() && keys[50]==0xAA);
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            c = Path(tmp) / 'settings.c'; c.write_text(code)
            for rows in (4, 5):
                binary = Path(tmp) / f'settings-{rows}'
                subprocess.run(['cc', '-std=c11', '-DHHKB_HYBRID=1', f'-DMATRIX_ROWS={rows}',
                                *(['-DHHKB_JIS=1'] if rows == 5 else []),
                                '-I', str(ROOT / 'keyboards/hhkb/modules/ec'),
                                '-I', str(ROOT / 'dmk_core/include'),
                                '-I', str(ROOT / 'keyboards/hhkb/modules/via_settings'), str(c), '-o', str(binary)], check=True)
                subprocess.run([str(binary)], check=True)

    def test_sleep_routing_probe_period_and_via_visibility(self):
        import json
        source = (ROOT / 'keyboards/hhkb/modules/ec/matrix.c').read_text()
        first = function(source, 'if (!critical && !hhkb_bt_matrix_wake())')
        asleep = function(source, 'if (!critical && !wake && !hhkb_bt_matrix_wake())')
        period = next(line.strip() for line in source.splitlines() if 'TickType_t probe_period =' in line)
        code = r'''
#include <stdbool.h>
#include <stdint.h>
#include <assert.h>
typedef uint32_t TickType_t;
#define pdMS_TO_TICKS(x) (x)
static bool enabled;
static uint8_t hz;
static unsigned off;
static bool hhkb_bt_matrix_wake(void){return enabled;}
static uint8_t hhkb_bt_sleep_hz(void){return hz;}
static void hhkb_hybrid_toggle_power(void){++off;}
static bool enter(bool critical){for(unsigned i=0;i<1;i++){
''' + first + r'''
return true;}return false;}
static bool stay(bool critical,bool wake){for(unsigned i=0;i<1;i++){
''' + asleep + r'''
return true;}return false;}
static TickType_t probe(void){
''' + period + r'''
return probe_period;}
int main(void){
 enabled=false;assert(!enter(false) && off==1);
 assert(enter(true) && off==1); // Critical warning is not bypassed.
 assert(!stay(false,false) && off==2); // Turning wake off during sleep.
 assert(stay(true,false) && stay(false,true) && off==2);
 enabled=true;assert(enter(false) && stay(false,false) && off==2);
 for(hz=1;hz<=10;hz++)assert(probe()==1000u/hz);
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            c = Path(tmp) / 'routing.c'; c.write_text(code)
            binary = Path(tmp) / 'routing'
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                            str(c), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)
        definition = json.loads((ROOT / 'keyboards/hhkb/via.json').read_text())
        def walk(node):
            if isinstance(node, dict):
                yield node
                for value in node.values(): yield from walk(value)
            elif isinstance(node, list):
                for value in node: yield from walk(value)
        bluetooth = next(n for n in walk(definition) if n.get('label') == 'Bluetooth')
        self.assertEqual(bluetooth['showIf'], '{id_hhkb_hybrid} == 1')
        controls = {n['label']: n for n in walk(bluetooth) if 'label' in n}
        for label in ('Sleep matrix scan rate (Hz)', 'Power off after sleep (minutes)'):
            self.assertEqual(controls[label]['showIf'], '{id_hhkb_bt_matrix_wake} == 1')
        self.assertEqual([o[1] for o in controls['Sleep matrix scan rate (Hz)']['options']], list(range(1,11)))
