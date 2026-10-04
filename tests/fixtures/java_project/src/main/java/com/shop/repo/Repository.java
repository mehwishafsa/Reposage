package com.shop.repo;

public interface Repository<T> {
    void save(T item);
}
