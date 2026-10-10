package com.shop;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public class OrderController {
    private static final Logger log = LoggerFactory.getLogger(OrderController.class);
    private final OrderService orders;

    public OrderController(OrderService orders) {
        this.orders = orders;
    }

    public Response create(OrderRequest req) {
        long start = System.currentTimeMillis();
        try {
            Order order = orders.placeOrder(req);
            log.info("POST /api/orders 200 {}ms", System.currentTimeMillis() - start);
            return Response.ok(order);
        } catch (CouponNotFoundException e) {
            log.warn("POST /api/orders 400 {}ms coupon not found: {}", System.currentTimeMillis() - start, e.getCode());
            return Response.badRequest("优惠券不存在");
        } catch (RuntimeException e) {
            log.error("POST /api/orders 500 {}ms couponCode={}", System.currentTimeMillis() - start, req.getCouponCode(), e);
            return Response.serverError();
        }
    }
}
