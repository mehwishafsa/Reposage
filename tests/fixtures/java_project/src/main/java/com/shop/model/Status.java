package com.shop.model;

public enum Status {
    NEW, PAID;

    boolean isFinal() {
        return this == PAID;
    }
}
