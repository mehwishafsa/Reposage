package com.shop;

import com.shop.model.*;
import com.shop.service.OrderService;

/** Command-line entry point. */
public class App {
    public static void main(String[] args) {
        OrderService service = new OrderService();
        Order order = service.place("book");
        System.out.println(order.describe());
    }
}
