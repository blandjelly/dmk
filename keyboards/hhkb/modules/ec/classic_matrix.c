#include "classic_matrix.h"
#include "ec_settings.h"
#include "ec_curve.h"
#include "rapid_trigger.h"
#ifdef HHKB_HYBRID
#include "hybrid.h"
#endif
#include "stm32l0xx.h"
#include "stm32l0xx_ll_adc.h"
#include "FreeRTOS.h"
#include "task.h"


static inline void delay_us_nop(uint32_t us) {
    for (volatile uint32_t i = 0; i < us * 6; i++) {
        __NOP();
    }
}

static inline void scan_wait_since(uint16_t start, uint16_t us) {
    if (!(RCC->APB1ENR & RCC_APB1ENR_TIM2EN)) {
        delay_us_nop(us);
        return;
    }
    uint16_t ticks = us * HHKB_SCAN_TIMER_TICKS_PER_US;
    while ((uint16_t)(TIM2->CNT - start) < ticks) {}
}
#define scan_delay_us(us) ((RCC->APB1ENR & RCC_APB1ENR_TIM2EN) ? scan_wait_since((uint16_t)TIM2->CNT, (us)) : delay_us_nop(us))

static bool s_oversampling_applied = false;

static inline uint16_t adc_read_sample(uint32_t saved_primask) {
    uint32_t timeout = 5000;
    while ((ADC1->CR & ADC_CR_ADSTP) && --timeout);
    if (!timeout) return UINT16_MAX;
    ADC1->ISR = ADC_ISR_EOC; // Discard completion left by a previously timed-out sample.
    ADC1->CR |= ADC_CR_ADSTART;
    // At the 1 MHz battery ADC clock, a 4x conversion outlasts a Nordic
    // SPI byte. Once triggered, acquisition is hardware-timed: let SPI run.
    if (ADC1->CFGR1 & ADC_CFGR1_AUTOFF) __set_PRIMASK(saved_primask);
    timeout = 5000;
    while (!(ADC1->ISR & ADC_ISR_EOC) && --timeout);
    if (!timeout) return UINT16_MAX;
    return (uint16_t)(ADC1->DR & 0x0FFF);
}

void hhkb_matrix_hardware_init(void) {
    // Enable GPIO clocks
    RCC->IOPENR |= RCC_IOPENR_GPIOAEN | RCC_IOPENR_GPIOBEN | RCC_IOPENR_GPIOCEN | RCC_IOPENR_GPIODEN;

    // Row drive pins (B0, B1, B2, B10) & Demux pins (B12, B13, B14, B15) as Push-Pull Output
    GPIOB->MODER &= ~(
        (3U << (0 * 2)) | (3U << (1 * 2)) | (3U << (2 * 2)) | (3U << (10 * 2)) | ((HHKB_ROW_MASK & 0x800U) ? (3U << 22) : 0U) |
        (3U << (12 * 2)) | (3U << (13 * 2)) | (3U << (14 * 2)) | (3U << (15 * 2))
    );
    GPIOB->MODER |= (
        (1U << (0 * 2)) | (1U << (1 * 2)) | (1U << (2 * 2)) | (1U << (10 * 2)) | ((HHKB_ROW_MASK & 0x800U) ? (1U << 22) : 0U) |
        (1U << (12 * 2)) | (1U << (13 * 2)) | (1U << (14 * 2)) | (1U << (15 * 2))
    );
    GPIOB->OSPEEDR |= (
        (3U << (0 * 2)) | (3U << (1 * 2)) | (3U << (2 * 2)) | (3U << (10 * 2)) | ((HHKB_ROW_MASK & 0x800U) ? (3U << 22) : 0U) |
        (3U << (12 * 2)) | (3U << (13 * 2)) | (3U << (14 * 2)) | (3U << (15 * 2))
    );

    // Row drive pins idle HIGH (unselected)
    GPIOB->BSRR = HHKB_ROW_MASK;

    // Analog control: PC6, PC7, PC8 and sensor board power PC14 as Push-Pull Output
    GPIOC->MODER &= ~((3U << (6 * 2)) | (3U << (7 * 2)) | (3U << (8 * 2)) | (3U << (14 * 2)));
    GPIOC->MODER |=  ((1U << (6 * 2)) | (1U << (7 * 2)) | (1U << (8 * 2)) | (1U << (14 * 2)));
    GPIOC->OSPEEDR |= ((3U << (6 * 2)) | (3U << (7 * 2)) | (3U << (8 * 2)) | (3U << (14 * 2)));
    GPIOC->BRR = (1U << 6) | (1U << 7) | (1U << 8);
    GPIOC->BSRR = (1U << 14); // Enable power for sensor board

    // ADC input: PA1 (IN1) as Analog
    GPIOA->MODER |= (3U << (1 * 2));

    // Enable ADC clock
    RCC->APB2ENR |= RCC_APB2ENR_ADC1EN;
    RCC->APB2RSTR |= RCC_APB2RSTR_ADC1RST;
    RCC->APB2RSTR &= ~RCC_APB2RSTR_ADC1RST;

    ADC1->CR |= ADC_CR_ADVREGEN;
    delay_us_nop(2000); // Restore conservative ADC regulator startup delay

    // Calibrate ADC
    ADC1->CR |= ADC_CR_ADCAL;
    uint32_t cal_timeout = 100000;
    while ((ADC1->CR & ADC_CR_ADCAL) && --cal_timeout);

    ADC1->CFGR1 = 0;
    ADC1->CFGR2 = LL_ADC_CLOCK_SYNC_PCLK_DIV2;
    ADC1->CHSELR = ADC_CHSELR_CHSEL1;
    ADC1->SMPR = 0; // 1.5 ADC clock cycles

    // Enable ADC
    ADC1->ISR = ADC_ISR_ADRDY;
    ADC1->CR |= ADC_CR_ADEN;
    uint32_t rdy_timeout = 100000;
    while (!(ADC1->ISR & ADC_ISR_ADRDY) && --rdy_timeout);

    // Initialize calibration, thresholds and rapid-trigger state before scanning.
    hhkb_calibration_load();
}

// Keep peripheral power sequencing out of the scarce scanner SRAM section.
__attribute__((noinline)) static void scan_adc_prepare(void) {
    bool requested = hhkb_get_oversampling();
#ifdef HHKB_HYBRID
    if (hhkb_hybrid_on_battery()) requested = hhkb_bt_oversampling();
#endif
    bool oversampling = requested && hhkb_get_effective_scan_rate() != 4;
    if (oversampling != s_oversampling_applied) {
        while (ADC1->CR & ADC_CR_ADSTART);
        ADC1->CR |= ADC_CR_ADDIS;
        while (ADC1->CR & ADC_CR_ADEN);
        if (oversampling)
            ADC1->CFGR2 |= ADC_CFGR2_OVSE | LL_ADC_OVS_RATIO_4 | LL_ADC_OVS_SHIFT_RIGHT_2;
        else
            ADC1->CFGR2 &= ~ADC_CFGR2_OVSE;
        if (!(ADC1->CFGR1 & ADC_CFGR1_AUTOFF)) {
            ADC1->ISR = ADC_ISR_ADRDY;
            ADC1->CR |= ADC_CR_ADEN;
            while (!(ADC1->ISR & ADC_ISR_ADRDY)) {}
        }
        s_oversampling_applied = oversampling;
    }

    if (!(ADC1->CFGR1 & ADC_CFGR1_AUTOFF) && !(ADC1->CR & ADC_CR_ADEN)) {
        while (ADC1->CR & ADC_CR_ADDIS) {}
        ADC1->ISR = ADC_ISR_ADRDY;
        ADC1->CR |= ADC_CR_ADEN;
        while (!(ADC1->ISR & ADC_ISR_ADRDY)) {}
    }
}

static void clamp_cfg_to_travel(rt_cfg_t *cfg, uint16_t max_travel) {
    if (cfg->act_pm > max_travel) cfg->act_pm = max_travel;
    if (cfg->press_pm > max_travel) cfg->press_pm = max_travel;
    if (cfg->release_pm > max_travel) cfg->release_pm = max_travel;
}

// Rapid Trigger profile of each key, the index into the scan's configurations ([0] classic).
static uint8_t s_key_profile[MATRIX_COLS][MATRIX_ROWS];

// Runs from flash: only the per-key loop needs the scarce RAM-resident code.
// Settings change rarely, so the configuration is rebuilt only after a change or a new curve:
// the scan must fit its 1 ms period, and rebuilding it every pass costs tens of microseconds.
__attribute__((noinline)) static void scan_cfg_prepare(rt_cfg_t cfgs[HHKB_RT_PROFILES + 1], const uint16_t *curve) {
    static uint32_t curve_revision;
    static uint32_t cfg_revision;
    bool curve_changed = curve_revision != ec_curve_revision;
    uint32_t revision = hhkb_scan_cfg_revision();
    if (!curve_changed && revision == cfg_revision) return;
    // Read the revision before the settings: a change in between only causes one more rebuild.
    cfg_revision = revision;
    if (curve_changed) {
        curve_revision = ec_curve_revision;
        for (uint8_t col = 0; col < MATRIX_COLS; ++col)
            for (uint8_t row = 0; row < MATRIX_ROWS; ++row)
                rt_key_reset(hhkb_get_rt_key(col, row));
    }
    uint8_t rt_lo[MATRIX_COLS], rt_hi[MATRIX_COLS]; // bit row: profile index bits 0 and 1
    hhkb_get_scan_cfgs(cfgs, rt_lo, rt_hi);
    uint16_t max_travel = curve ? curve[125] : 1000;
    for (unsigned p = 0; p <= HHKB_RT_PROFILES; p++)
        clamp_cfg_to_travel(&cfgs[p], max_travel);
    for (uint8_t col = 0; col < MATRIX_COLS; ++col) {
        for (uint8_t row = 0; row < MATRIX_ROWS; ++row) {
            uint16_t act = hhkb_get_key_act_pm(col, row);
            hhkb_get_rt_key(col, row)->act_pm = act > max_travel ? max_travel : act;
            s_key_profile[col][row] = (uint8_t)(((rt_lo[col] >> row) & 1U) | (((rt_hi[col] >> row) & 1U) << 1));
        }
    }
}

__attribute__((noinline)) static void scan_adc_idle(void) {
#ifdef HHKB_HYBRID
    if (hhkb_hybrid_on_battery()) {
        while (ADC1->CR & ADC_CR_ADSTART) {}
        ADC1->CR |= ADC_CR_ADDIS;
        while (ADC1->CR & ADC_CR_ADEN) {}
    }
#endif
}

typedef struct {
    matrix_row_t *matrix;
    const rt_cfg_t *cfgs;
    const uint16_t *curve;
    bool capture;
    bool capture_failed;
    bool is_calibrating;
    bool debug_topre;
    bool changed;
} scan_ctx_t;

// The reading depends on how long each key's scan step takes. Recording calibration samples
// inline makes those steps longer than in normal use and biases the baseline upward (about 50
// ADC counts on Hybrid ANSI). Where RAM allows, the scan only stores the samples and the
// calibration runs after the loop. Hybrid JIS has under 100 bytes of RAM left for this buffer.
#if !(defined(HHKB_HYBRID) && defined(HHKB_JIS))
#define HHKB_CAL_DEFERRED 1
static uint16_t s_cal_vals[MATRIX_COLS][MATRIX_ROWS];

__attribute__((noinline)) static void scan_calibrate_deferred(bool capture) {
    for (uint8_t col = 0; col < MATRIX_COLS; col++) {
        for (uint8_t row = 0; row < MATRIX_ROWS; row++) {
            uint16_t val = s_cal_vals[col][row];
            if (val == UINT16_MAX) continue; // already counted as a failed sample
            if (capture) hhkb_cal_accumulate_baseline(col, row, val);
            else hhkb_cal_record_peak(col, row, val);
        }
    }
}
#endif

// Updates one key from its ADC sample. RAM-resident: it runs for every key of every scan.
__attribute__((section(".RamFunc"), optimize("O2"), noinline))
static void scan_process(scan_ctx_t *ctx, uint8_t col, uint8_t row, uint16_t val) {
    matrix_row_t *matrix_row = &ctx->matrix[row];
    matrix_row_t prev_row = *matrix_row;
    rt_key_t *key = &s_rt_keys[col][row];
    bool is_pressed;

#ifdef HHKB_CAL_DEFERRED
    if (ctx->is_calibrating) s_cal_vals[col][row] = val;
#endif
    if (val == UINT16_MAX) {
        // Never calibrate or update RT state from a stale ADC data register.
        if (ctx->capture) ctx->capture_failed = true;
        is_pressed = key->down;
    } else {
#ifndef HHKB_CAL_DEFERRED
        if (ctx->is_calibrating) {
            if (ctx->capture) {
                hhkb_cal_accumulate_baseline(col, row, val);
            } else {
                hhkb_cal_record_peak(col, row, val);
            }
        }
#endif
        // Calibrated endpoints stay fixed outside an explicit calibration.
        // A single runtime spike must not permanently expand the range.
        // At or below the baseline this lands in the top dead zone and releases the key, the same
        // as a separate shortcut would, but in the same time as a reading just above it.
        is_pressed = rt_update_curve(key, &ctx->cfgs[s_key_profile[col][row]], val, ctx->curve);

        // Feed debug monitoring only when EC debug streaming is active
        if (__builtin_expect(ctx->debug_topre, 0)) {
            uint16_t norm = rt_normalize_curve(key, val, ctx->curve);
            hhkb_debug_feed_sample(col, row, val, norm, is_pressed);
        }
    }

    if (is_pressed) *matrix_row |= (matrix_row_t)(1U << col);
    else *matrix_row &= (matrix_row_t)~(1U << col);
    if (*matrix_row != prev_row) ctx->changed = true;
}

#ifdef HHKB_SCAN_DIAGNOSTICS
#include "usb_descriptors.h"
#include "layers.h"
// Last ADC sample of every key: one store per key, so the scan timing stays as it is.
static uint16_t s_diag_raw[MATRIX_COLS][MATRIX_ROWS];
void hhkb_rest_report(void) {
    int32_t sum = 0;
    int min = 32767, max = -32768;
    unsigned n = 0;
    for (uint8_t c = 0; c < MATRIX_COLS; c++) {
        for (uint8_t r = 0; r < MATRIX_ROWS; r++) {
            if (keyboard_get_flat_key_index(r, c) < 0) continue;
            int raw = s_diag_raw[c][r], base = hhkb_get_cal_baseline(c, r), peak = hhkb_get_cal_peak(c, r);
            if (raw == 0 || raw == UINT16_MAX || raw > base + (peak - base) / 5) continue; // pressed
            int d = raw - base;
            sum += d;
            n++;
            if (d < min) min = d;
            if (d > max) max = d;
        }
    }
    debug_console_printf("[REST] keys=%u raw-base avg=%d min=%d max=%d | R0C1 raw=%u base=%u\r\n", n,
                         n ? (int)(sum / (int32_t)n) : 0, n ? min : 0, n ? max : 0, s_diag_raw[1][0],
                         hhkb_get_cal_baseline(1, 0));
}
#endif

__attribute__((section(".RamFunc"), optimize("O2")))
bool hhkb_matrix_scan_raw(matrix_row_t current_matrix[]) {
#ifdef HHKB_HYBRID
    // Stock scan wrapper reasserts sensor power and keeps PD2 LOW.
    GPIOC->BSRR = 1U << 14;
    GPIOD->BRR = 1U << 2;
#endif
    hhkb_record_matrix_scan();
    scan_adc_prepare();

    uint16_t row_settle_us = (ADC1->CFGR1 & ADC_CFGR1_AUTOFF) ? 25 : 3;
    static const uint16_t row_pins[MATRIX_ROWS] = HHKB_ROW_BITS;
    static const uint32_t col_mux_bsrr[MATRIX_COLS] = {
        0x70000000, 0x60001000, 0x50002000, 0x40003000,
        0x30004000, 0x20005000, 0x10006000, 0x7000,
        0x70000000, 0x60001000, 0x50002000, 0x40003000,
        0x30004000, 0x20005000, 0x10006000
    };

    GPIOB->BSRR = HHKB_ROW_MASK;    // Deselect all rows
    GPIOC->BSRR = (1U << 7); // Enable OpAmp

    GPIOC->BSRR = (1U << 6);
    GPIOB->BRR  = (1U << 15);

    scan_ctx_t ctx = {
        .matrix = current_matrix,
        .curve = ec_curve_lut,
        .capture = hhkb_cal_is_capturing_baseline(),
        .is_calibrating = hhkb_is_calibrating(),
        .debug_topre = hhkb_get_debug_flag(DEBUG_FLAG_TOPRE),
    };
    // [0] classic, [1..3] Rapid Trigger profiles. Static: only the matrix task scans, and its
    // 1 KB stack also carries the debug printf path.
    static rt_cfg_t cfgs[HHKB_RT_PROFILES + 1];
    scan_cfg_prepare(cfgs, ctx.curve);
    ctx.cfgs = cfgs;

    // The previous key's sample is processed while C57 discharges for the next one, so the
    // key logic costs no scan time of its own. Each strobe still follows the previous row's
    // release (or the column switch) by the same 4 us as before: 2 us settle + 2 us discharge.
    // C57 is now held discharged for the whole interval instead of its last 2 us.
    bool pending = false;
    uint8_t pending_col = 0, pending_row = 0;
    uint16_t pending_val = 0;

    for (uint8_t col = 0; col < MATRIX_COLS; col++) {
        if (col == 8) {
            GPIOC->BRR  = (1U << 6);
            GPIOB->BSRR = (1U << 15);
        }

        GPIOB->BSRR = col_mux_bsrr[col];
        uint16_t settle_start = (uint16_t)TIM2->CNT;

        for (uint8_t row = 0; row < MATRIX_ROWS; row++) {
#ifdef HHKB_JIS
            static const uint8_t populated[MATRIX_COLS] = {29, 27, 29, 31, 29, 29, 31, 29, 31, 31, 31, 31, 31, 31, 31};
            if (!(populated[col] & (1U << row))) continue;
#endif
            // Discharge capacitor C57
            GPIOC->BSRR = (1U << 8);
            if (pending) scan_process(&ctx, pending_col, pending_row, pending_val);
            scan_wait_since(settle_start, 4);

            uint32_t saved_primask = __get_PRIMASK();
            __disable_irq();
            GPIOC->BRR  = (1U << 8);

            // Drive row strobe active (LOW)
            GPIOB->BRR = row_pins[row];
            scan_delay_us(row_settle_us);

            uint16_t val = adc_read_sample(saved_primask);
            GPIOB->BSRR = row_pins[row];

            __set_PRIMASK(saved_primask);

            settle_start = (uint16_t)TIM2->CNT;
#ifdef HHKB_SCAN_DIAGNOSTICS
            s_diag_raw[col][row] = val;
#endif
            pending = true;
            pending_col = col;
            pending_row = row;
            pending_val = val;
        }
    }
    if (pending) scan_process(&ctx, pending_col, pending_row, pending_val);
#ifdef HHKB_CAL_DEFERRED
    if (ctx.is_calibrating) scan_calibrate_deferred(ctx.capture);
#endif
    bool capture = ctx.capture, capture_failed = ctx.capture_failed;
    bool changed = ctx.changed;

    if (capture) {
        // A missing sample must not bias the 16-frame baseline average.
        if (capture_failed) hhkb_calibration_start();
        else hhkb_cal_decrement_samples();
    }

    // Disable OpAmp
    GPIOC->BRR = (1U << 7);
    scan_adc_idle();
    return changed;
}
