package com.shop.model;

/**
 * An order for a single item.
 */
public class Order {
    private final String item;
    private Status status = Status.NEW;

    public Order(String item) {
        this.item = item;
    }

    @Override
    public String toString() {
        return describe();
    }

    public String describe() {
        return format(item);
    }

    private String format(String text) {
        return "Order: " + text + (status.isFinal() ? " (done)" : "");
    }
}
