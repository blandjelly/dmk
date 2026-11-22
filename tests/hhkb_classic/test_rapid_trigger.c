/* Host test; run via tests/run_all_tests.sh. */
#include "rapid_trigger.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>

#define BASE 800
#define PEAK 1800                       /* range 1000 counts -> 1 count == 1 permille */

static rt_key_t key;
static rt_cfg_t cfg;

static uint16_t raw(int pm) { return (uint16_t)(BASE + pm); }
static bool feed(int pm) { return rt_update(&key, &cfg, raw(pm)); }

static void setup(bool rt) {
    rt_cfg_defaults(&cfg);
    cfg.rt_enabled = rt;
    rt_key_set_range(&key, BASE, PEAK);
    rt_key_reset(&key);
    key.act_pm = 0; /* configuration, not state: rt_key_reset keeps it */
}

int main(void) {
    int events;

    /* 1. Slow press: nothing before act point (750), pressed right at it. */
    setup(true);
    for (int pm = 0; pm < 750; pm += 5) assert(!feed(pm));
    assert(feed(750));

    /* 2. Mid-travel release: 20 up is ignored, 30 up releases. */
    for (int pm = 750; pm <= 1000; pm += 10) assert(feed(pm));
    assert(feed(980));
    assert(feed(971));
    assert(!feed(970));

    /* 3. Re-press without returning to the top: 20 down ignored, 30 down presses. */
    assert(!feed(980));
    assert(!feed(990));
    assert(feed(1000));

    /* 4. Rapid wiggle in 800..850 produces many transitions. */
    setup(true);
    for (int pm = 0; pm <= 850; pm += 10) feed(pm);
    events = 0;
    bool prev = feed(850);
    for (int i = 0; i < 10; i++) {
        for (int pm = 850; pm >= 800; pm -= 5) { bool s = feed(pm); if (s != prev) { events++; prev = s; } }
        for (int pm = 800; pm <= 850; pm += 5) { bool s = feed(pm); if (s != prev) { events++; prev = s; } }
    }
    assert(events == 20);

    /* 5. Going to the top dead zone disarms RT; partial press below act does not trigger. */
    feed(40);
    assert(!feed(400));
    assert(!feed(749));
    assert(feed(750));

    /* 6. Noise +-8 around a held key does not chatter (needs 30 drop from max). */
    setup(true);
    for (int pm = 0; pm <= 900; pm += 10) feed(pm);
    srand(1);
    for (int i = 0; i < 100000; i++) assert(feed(900 + (rand() % 17) - 8));

    /* 7. Noise +-8 below the actuation point never presses. */
    setup(true);
    for (int i = 0; i < 100000; i++) assert(!feed(720 + (rand() % 17) - 8));

    /* 8. Classic mode: fixed act 750, hysteresis = release_pm (30). */
    setup(false);
    assert(!feed(749));
    assert(feed(750));
    assert(feed(721));
    assert(!feed(719));
    assert(!feed(730));
    assert(feed(750));

    /* 9. Calibration fallback: peak <= base+150 does not divide by ~0. */
    rt_key_set_range(&key, BASE, BASE + 10);
    assert(rt_normalize(&key, BASE + 500) == 500);

    /* 10. Per-key actuation overrides the shared point; classic hysteresis follows it. */
    setup(false);
    key.act_pm = 300;
    assert(!feed(299));
    assert(feed(300));
    assert(feed(271));
    assert(!feed(270));
    assert(feed(500));

    /* 11. Rapid Trigger arms at the per-key point, then follows movement as before. */
    setup(true);
    key.act_pm = 200;
    for (int pm = 0; pm < 200; pm += 5) assert(!feed(pm));
    assert(feed(200));
    assert(feed(400));
    assert(feed(371));
    assert(!feed(370));
    assert(feed(400));

    /* 12. The top dead zone still disarms; re-arming needs the per-key point again. */
    feed(40);
    assert(!feed(199));
    assert(feed(200));

    /* 13. Hardware logs (key R2 C1, base 179, peak 2864): a bottomed-out key held for 10 s.
     * Raw rises past the peak when pressed harder and falls back when the finger relaxes,
     * without any key movement. RT press 0.4 mm, act 0.5 mm, top dead zone 0.1 mm. */
    static const uint16_t press_raw[] = {318, 364, 406, 486, 621, 726, 1002, 1495, 1964, 2552, 2825, 2868, 2913, 2954};
    /* Earlier firmware released at 2806 (release 0.2 mm) and at 2622 (release 0.5 mm):
     * its deepest point followed the over-travel up to 110 %. */
    static const uint16_t hold_02[] = {2913, 2954, 2905, 2806, 2755, 2913, 2954, 2806};
    static const uint16_t hold_05[] = {2950, 2865, 2907, 2856, 2802, 2622, 2950, 2622};
    for (int release = 50; release <= 125; release += 75) {
        rt_cfg_defaults(&cfg);
        cfg.rt_enabled = true;
        cfg.bottom_dz = 5;             /* default 0.5 mm: deepest tracked point 875 */
        cfg.act_pm = 125;
        cfg.press_pm = 100;
        cfg.release_pm = (uint16_t)release;
        cfg.reset_pm = 25;
        const uint16_t *hold = release == 50 ? hold_02 : hold_05;
        unsigned hold_len = release == 50 ? sizeof(hold_02) / sizeof(hold_02[0]) : sizeof(hold_05) / sizeof(hold_05[0]);
        rt_key_set_range(&key, 179, 2864);
        rt_key_reset(&key);
        key.act_pm = 0;

        /* The key stays pressed for the whole 10 s (10000 scans). */
        for (unsigned i = 0; i < sizeof(press_raw) / sizeof(press_raw[0]); i++)
            assert(rt_update(&key, &cfg, press_raw[i]) == (i >= 4));
        for (int n = 0; n < 1250; n++)
            for (unsigned i = 0; i < hold_len; i++) assert(rt_update(&key, &cfg, hold[i]));
        assert(key.ext == 875);

        /* A real lift still releases, exactly `release` above the zone edge. */
        uint16_t lift = 2864;
        while (rt_normalize(&key, lift) > 875 - release) lift--;
        assert(rt_update(&key, &cfg, (uint16_t)(lift + 1)));
        assert(!rt_update(&key, &cfg, lift));
    }

    /* 14. Bottom dead zone: hold at the bottom, small lift releases, pressing back re-presses. */
    setup(true);
    cfg.bottom_dz = 5;                 /* deepest tracked point 875 */
    cfg.press_pm = 100;
    cfg.release_pm = 50;
    for (int pm = 0; pm <= 1100; pm += 10) assert(feed(pm) == (pm >= 750));
    for (int pm = 1100; pm >= 826; pm -= 2) assert(feed(pm));
    assert(feed(826));
    assert(!feed(825));
    assert(!feed(900));
    assert(!feed(924));
    assert(feed(925));                 /* 100 below the lowest point (825) */
    assert(feed(1050));
    assert(!feed(825));

    /* 15. Actuation point inside the bottom dead zone: arms there, releases below the zone. */
    setup(true);
    cfg.bottom_dz = 5;
    key.act_pm = 950;
    for (int pm = 0; pm < 950; pm += 10) assert(!feed(pm));
    assert(feed(950));
    assert(feed(1000));
    assert(feed(846));                 /* default release_pm is 30 */
    assert(!feed(845));

    /* 16. With a travel curve the zone is measured from the curve's maximum travel. */
    {
        static uint16_t lut[126];
        for (unsigned i = 0; i < 126; i++) lut[i] = (uint16_t)(i * 7); /* 3.5 mm max travel: 875 */
        setup(true);
        cfg.bottom_dz = 4;             /* deepest tracked point 875 - 100 = 775 */
        for (int pm = 0; pm <= 1100; pm += 10) rt_update_curve(&key, &cfg, raw(pm), lut);
        assert(key.down && key.ext == 775);
        assert(rt_update_curve(&key, &cfg, raw(1000), lut));
        /* 745 travel units = 745 / 0.875 permille of the signal; release_pm is 30. */
        assert(rt_update_curve(&key, &cfg, raw(853), lut));
        assert(!rt_update_curve(&key, &cfg, raw(851), lut));
    }

    puts("rapid trigger: all tests passed");
    return 0;
}
