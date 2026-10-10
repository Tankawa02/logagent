package com.shop;

public class OrderService {
    private final InventoryClient inventory;
    private final CouponRepository coupons;

    public OrderService(InventoryClient inventory, CouponRepository coupons) {
        this.inventory = inventory;
        this.coupons = coupons;
    }

    public Order placeOrder(OrderRequest req) {
        inventory.reserve(req.getSku(), req.getQty());
        long discount = 0;
        if (req.getCouponCode() != null) {
            Coupon coupon = coupons.findValid(req.getCouponCode());
            discount = coupon.getAmount();
        }
        return new Order(req.getSku(), req.getQty(), req.getPrice() * req.getQty() - discount);
    }
}
