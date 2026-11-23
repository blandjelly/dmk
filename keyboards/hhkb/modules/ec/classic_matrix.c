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

// Release to strobe interval of every key. The sensor reading depends on it: about 50 ADC counts
// per microsecond here, so it must not vary with the work done meanwhile. The key logic below
// runs inside it and must stay shorter (about 4 us at 32 MHz).
#define SCAN_GAP_US 6U

#ifdef HHKB_SCAN_DIAGNOSTICS
static volatile uint32_t s_scan_gap_late;
uint32_t hhkb_scan_gap_late(void) { return s_scan_gap_late; }
#endif

// Updates one key from its ADC sample. RAM-resident: it runs for every key of every scan.
__attribute__((section(".RamFunc"), optimize("O2"), noinline))
static bool scan_process(scan_ctx_t *ctx, uint8_t col, uint8_t row, uint16_t val) {
    matrix_row_t *matrix_row = &ctx->matrix[row];
    matrix_row_t prev_row = *matrix_row;
    rt_key_t *key = &s_rt_keys[col][row];
    bool is_pressed;

    if (val == UINT16_MAX) {
        // Never calibrate or update RT state from a stale ADC data register.
        if (ctx->capture) ctx->capture_failed = true;
        is_pressed = key->down;
    } else if (val <= key->base) {
        // Calibrated endpoints stay fixed outside an explicit calibration.
        // A single runtime spike must not permanently expand the range.
        key->ext = 0;
        key->down = false;
        key->latched = false;
        is_pressed = false;
    } else {
        is_pressed = rt_update_curve(key, &ctx->cfgs[s_key_profile[col][row]], val, ctx->curve);
    }

    if (is_pressed) *matrix_row |= (matrix_row_t)(1U << col);
    else *matrix_row &= (matrix_row_t)~(1U << col);
    if (*matrix_row != prev_row) ctx->changed = true;
    return is_pressed;
}

// Outside the release-to-strobe gap and rarely enabled: these stay in flash to save RAM.
__attribute__((noinline)) static void scan_debug_feed(const scan_ctx_t *ctx, uint8_t col, uint8_t row,
                                                      uint16_t val, bool down) {
    hhkb_debug_feed_sample(col, row, val, rt_normalize_curve(&s_rt_keys[col][row], val, ctx->curve), down);
}

__attribute__((noinline)) static void scan_calibrate(const scan_ctx_t *ctx, uint8_t col, uint8_t row, uint16_t val) {
    if (ctx->capture) hhkb_cal_accumulate_baseline(col, row, val);
    else hhkb_cal_record_peak(col, row, val);
}

__attribute__((section(".RamFunc"), optimize("O2")))
bool hhkb_matrix_scan_raw(matrix_row_t current_matrix[]) {
#ifdef HHKB_HYBRID
    // Stock scan wrapper reasserts sensor power and keeps PD2 LOW.
    GPIOC->BSRR = 1U << 14;
    GPIOD->BRR = 1U << 2;
#endif
    hhkb_record_matrix_scan();
    scan_adc_prepare();

    // Battery: slow ADC clock and CPU, interrupts stay enabled outside the analog sample.
    const bool slow = (ADC1->CFGR1 & ADC_CFGR1_AUTOFF) != 0;
    const uint16_t row_settle_us = slow ? 25 : 3;
    const uint32_t saved_primask = __get_PRIMASK();
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

    // Each key: release of the previous row (or the column switch), SCAN_GAP_US with C57
    // discharged, strobe, settle, ADC. The previous key's logic runs during that gap, so it costs
    // no scan time of its own. Interrupts, calibration and debug output run while the sampled row
    // is still strobed, never inside the gap, which keeps the gap and the readings constant.
    bool pending = false, pending_down = false;
    uint8_t pending_col = 0, pending_row = 0;
    uint16_t pending_val = 0;

    for (uint8_t col = 0; col < MATRIX_COLS; col++) {
        if (!slow) __disable_irq();
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
            if (pending) pending_down = scan_process(&ctx, pending_col, pending_row, pending_val);
#ifdef HHKB_SCAN_DIAGNOSTICS
            if ((uint16_t)((uint16_t)TIM2->CNT - settle_start) >= SCAN_GAP_US * HHKB_SCAN_TIMER_TICKS_PER_US)
                s_scan_gap_late++;
#endif
            scan_wait_since(settle_start, SCAN_GAP_US);

            __disable_irq();
            GPIOC->BRR  = (1U << 8);

            // Drive row strobe active (LOW)
            GPIOB->BRR = row_pins[row];
            scan_delay_us(row_settle_us);

            uint16_t val = adc_read_sample(saved_primask);

            __set_PRIMASK(saved_primask);
            if (__builtin_expect(ctx.debug_topre, 0) && pending)
                scan_debug_feed(&ctx, pending_col, pending_row, pending_val, pending_down);
            if (__builtin_expect(ctx.is_calibrating, 0) && val != UINT16_MAX) scan_calibrate(&ctx, col, row, val);
            if (!slow) __disable_irq();

            GPIOB->BSRR = row_pins[row];
            settle_start = (uint16_t)TIM2->CNT;
            pending = true;
            pending_col = col;
            pending_row = row;
            pending_val = val;
        }
    }
    __set_PRIMASK(saved_primask);
    if (pending) {
        pending_down = scan_process(&ctx, pending_col, pending_row, pending_val);
        if (__builtin_expect(ctx.debug_topre, 0)) scan_debug_feed(&ctx, pending_col, pending_row, pending_val, pending_down);
    }
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
