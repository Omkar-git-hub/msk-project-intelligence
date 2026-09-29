package com.msk.payment;

public class PaymentService {
    private String apiKey;

    public PaymentService(String apiKey) {
        this.apiKey = apiKey;
    }

    public boolean process(String orderId, double amount) {
        return amount > 0;
    }
}
