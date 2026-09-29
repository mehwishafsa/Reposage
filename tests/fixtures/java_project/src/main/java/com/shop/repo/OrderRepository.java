package com.shop.repo;

import com.shop.model.Order;
import java.util.ArrayList;
import java.util.List;

public class OrderRepository implements Repository<Order> {
    private final List<Order> items = new ArrayList<>();

    @Override
    public void save(Order item) {
        items.add(item);
    }
}
