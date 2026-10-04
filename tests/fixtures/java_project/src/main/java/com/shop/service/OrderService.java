package com.shop.service;

import com.shop.model.Order;
import com.shop.repo.OrderRepository;

public class OrderService {
    private final OrderRepository repo = new OrderRepository();

    /** Place an order for one item. */
    public Order place(String item) {
        Order order = new Order(item);
        repo.save(order);
        audit(order);
        return order;
    }

    public Order place(String item, int quantity) {
        return place(item);
    }

    private void audit(Order order) {
        Helpers.log("placed " + order.describe());
    }
}
