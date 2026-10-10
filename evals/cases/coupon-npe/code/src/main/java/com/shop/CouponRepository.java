package com.shop;

import java.time.Clock;
import java.time.Instant;
import java.util.Map;

public class CouponRepository {
    private final Map<String, Coupon> store;
    private final Clock clock;

    public CouponRepository(Map<String, Coupon> store, Clock clock) {
        this.store = store;
        this.clock = clock;
    }

    /** 按券码查询可用的优惠券。 */
    public Coupon findValid(String code) {
        Coupon coupon = store.get(code);
        if (coupon == null) {
            throw new CouponNotFoundException(code);
        }
        Instant now = clock.instant();
        if (coupon.getExpireAt().isBefore(now)) {
            return null;
        }
        return coupon;
    }
}
