"""Host checks for boot-switch debounce, ADC timeout and debug persistence."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from .test_hybrid_button import ROOT, function


class ReviewRegressionTest(unittest.TestCase):
    def run_c(self, code):
        with tempfile.TemporaryDirectory() as tmp:
            c = Path(tmp) / 'test.c'; c.write_text(code)
            exe = Path(tmp) / 'test'
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                            str(c), '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True)

    def test_sw6_requires_ten_consecutive_on_samples(self):
        source = (ROOT / 'keyboards/hhkb/modules/dip_switch/dip_switch.c').read_text()
        self.run_c(r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
static unsigned calls,high_sample;
static struct {uint32_t IDR;} gpio;
static unsigned SystemCoreClock=1000;
static void __NOP(void){}
static void *next_gpio(void){gpio.IDR=(++calls==high_sample)?16:0;return &gpio;}
#define GPIOC ((__typeof__(&gpio))next_gpio())
''' + function(source, 'static bool sw6_stable_on(') + r'''
int main(void){
 for(high_sample=1;high_sample<=10;high_sample++){
  calls=0;assert(!sw6_stable_on());assert(calls==high_sample);
 }
 high_sample=0;calls=0;assert(sw6_stable_on() && calls==10);
}
''')

    def test_adc_timeout_does_not_return_stale_dr(self):
        source = (ROOT / 'keyboards/hhkb/modules/ec/classic_matrix.c').read_text()
        # Model write-one-to-clear ISR semantics in the memory-backed register mock.
        sample = function(source, 'static inline uint16_t adc_read_sample(').replace(
            'ADC1->ISR = ADC_ISR_EOC;', 'ADC1->ISR = 0;')
        self.run_c(r'''
#include <assert.h>
#include <stdint.h>
#define ADC_CR_ADSTP 1
#define ADC_CR_ADSTART 2
#define ADC_CFGR1_AUTOFF 1
#define ADC_ISR_EOC 1
static struct {uint32_t CR,ISR,CFGR1,DR;} adc;
#define ADC1 (&adc)
static void __set_PRIMASK(uint32_t p){(void)p;}
''' + sample + r'''
int main(void){
 adc.DR=1234;adc.CR=ADC_CR_ADSTP;
 assert(adc_read_sample(0)==UINT16_MAX);
 adc.CR=0;adc.ISR=ADC_ISR_EOC; // Stale completion must also be discarded.
 assert(adc_read_sample(0)==UINT16_MAX);
}
''')

    def test_all_debug_flags_mark_settings_dirty_only_on_change(self):
        source = (ROOT / 'keyboards/hhkb/modules/ec/ec_settings.c').read_text()
        self.run_c(r'''
#include <stdint.h>
#include <stdbool.h>
#include <assert.h>
static uint8_t s_debug_flags;
static unsigned dirty;
static void hhkb_settings_mark_dirty(void){++dirty;}
''' + function(source, 'void hhkb_set_debug_flag(') + r'''
int main(void){
 for(unsigned flag=1;flag<=8;flag<<=1){
  unsigned before=dirty;
  hhkb_set_debug_flag(flag,true);assert(dirty==before+1);
  hhkb_set_debug_flag(flag,true);assert(dirty==before+1);
  hhkb_set_debug_flag(flag,false);assert(dirty==before+2);
 }
}
''')

    def test_low_resting_baseline_survives_reload(self):
        # A Hybrid key rested at 99 counts; a 100-count floor reset it to the default after power-up.
        source = (ROOT / 'keyboards/hhkb/modules/ec/ec_settings.c').read_text()
        floor = next(l for l in source.splitlines() if l.startswith('#define HHKB_CAL_BASE_MIN'))
        self.run_c(r'''
#include <assert.h>
#include <stdint.h>
#define MATRIX_COLS 1
#define MATRIX_ROWS 3
#define RT_MIN_RANGE 150
''' + floor + r'''
typedef struct {uint16_t baseline[MATRIX_COLS][MATRIX_ROWS], peak[MATRIX_COLS][MATRIX_ROWS];} hhkb_eeprom_cal_t;
static uint16_t s_cal_baseline[MATRIX_COLS][MATRIX_ROWS] = {{800, 800, 800}};
static uint16_t s_cal_peak[MATRIX_COLS][MATRIX_ROWS] = {{3000, 3000, 3000}};
''' + function(source, 'static void load_cal_ranges(') + r'''
int main(void){
 hhkb_eeprom_cal_t cal = {{{99, 0, 186}}, {{2916, 0, 2700}}};
 load_cal_ranges(&cal);
 assert(s_cal_baseline[0][0] == 99 && s_cal_peak[0][0] == 2916);
 assert(s_cal_baseline[0][1] == 800 && s_cal_peak[0][1] == 3000); /* erased record keeps defaults */
 assert(s_cal_baseline[0][2] == 186 && s_cal_peak[0][2] == 2700);
}
''')

    def test_calibration_fast_path_matches_full_update(self):
        # hhkb_cal_process_scan skips keys that cannot change; feeding every key to
        # hhkb_cal_record_peak on the scans that cover it must give the same state.
        source = (ROOT / 'keyboards/hhkb/modules/ec/ec_settings.c').read_text()
        record = function(source, 'void hhkb_cal_record_peak(')
        process = function(source, 'void hhkb_cal_process_scan(')
        body = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#define MATRIX_COLS 15
#define MATRIX_ROWS 4
#define NUM_KEYS 60
#define RT_MIN_RANGE 150
#define MATRIX_MASK_WORDS 2
#define DEBUG_FLAG_CAL 8
static bool hhkb_get_debug_flag(uint8_t f){(void)f;return false;}
static void debug_console_printf(const char *f, ...){(void)f;}
static uint8_t calibrated_key_count(void){return 0;}
#define S(p) \
 static uint16_t p##s_capture_base[MATRIX_COLS][MATRIX_ROWS], p##s_capture_peak[MATRIX_COLS][MATRIX_ROWS], \
   p##s_cal_press_start_peak[MATRIX_COLS][MATRIX_ROWS]; \
 static uint32_t p##s_cal_down_mask[MATRIX_MASK_WORDS], p##s_calibrated_mask[MATRIX_MASK_WORDS];
S(a_) S(b_)
#define s_capture_base a_s_capture_base
#define s_capture_peak a_s_capture_peak
#define s_cal_press_start_peak a_s_cal_press_start_peak
#define s_cal_down_mask a_s_cal_down_mask
#define s_calibrated_mask a_s_calibrated_mask
#define hhkb_cal_record_peak ref_record
''' + record + r'''
#undef s_capture_base
#undef s_capture_peak
#undef s_cal_press_start_peak
#undef s_cal_down_mask
#undef s_calibrated_mask
#undef hhkb_cal_record_peak
#define s_capture_base b_s_capture_base
#define s_capture_peak b_s_capture_peak
#define s_cal_press_start_peak b_s_cal_press_start_peak
#define s_cal_down_mask b_s_cal_down_mask
#define s_calibrated_mask b_s_calibrated_mask
''' + record + '\n' + process + r'''
int main(void){
 uint16_t vals[MATRIX_COLS][MATRIX_ROWS];
 srand(1);
 for (unsigned k = 0; k < 60; k++) {
  uint16_t base = (uint16_t)(90 + rand() % 900);
  (&a_s_capture_base[0][0])[k] = (&b_s_capture_base[0][0])[k] = base;
  (&a_s_capture_peak[0][0])[k] = (&b_s_capture_peak[0][0])[k] = base;
 }
 unsigned half = 0;
 for (int scan = 0; scan < 20000; scan++) {
  for (unsigned k = 0; k < 60; k++) {
   uint16_t base = (&a_s_capture_base[0][0])[k];
   int r = rand() % 10;
   uint16_t v = r == 0 ? UINT16_MAX : (uint16_t)(base + (r < 6 ? rand() % 40 - 20 : rand() % 400 - 40));
   (&vals[0][0])[k] = v;
  }
  hhkb_cal_process_scan(vals, false);
  for (unsigned k = half ? 30 : 0; k < (half ? 60u : 30u); k++)
   if ((&vals[0][0])[k] != UINT16_MAX) ref_record((uint8_t)(k / 4), (uint8_t)(k % 4), (&vals[0][0])[k]);
  half = !half;
  assert(!memcmp(a_s_capture_peak, b_s_capture_peak, sizeof a_s_capture_peak));
  assert(!memcmp(a_s_cal_down_mask, b_s_cal_down_mask, sizeof a_s_cal_down_mask));
  assert(!memcmp(a_s_calibrated_mask, b_s_calibrated_mask, sizeof a_s_calibrated_mask));
  assert(!memcmp(a_s_cal_press_start_peak, b_s_cal_press_start_peak, sizeof a_s_cal_press_start_peak));
 }
 assert(a_s_calibrated_mask[0] && a_s_calibrated_mask[1]);
}
'''
        self.run_c(body)

    def test_incomplete_baseline_restarts_instead_of_averaging_missing_sample(self):
        source = (ROOT / 'keyboards/hhkb/modules/ec/classic_matrix.c').read_text()
        block = function(source, 'if (capture) {\n        // A missing sample')
        self.run_c(r'''
#include <stdbool.h>
#include <assert.h>
static unsigned restarts,decrements;
static void hhkb_calibration_start(void){++restarts;}
static void hhkb_cal_decrement_samples(void){++decrements;}
static void frame(bool capture,bool capture_failed){
''' + block + r'''
}
int main(void){
 frame(true,true);assert(restarts==1 && decrements==0);
 frame(true,false);assert(restarts==1 && decrements==1);
 frame(false,true);assert(restarts==1 && decrements==1);
}
''')
