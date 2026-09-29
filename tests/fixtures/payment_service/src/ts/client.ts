export interface PaymentRequest {
    orderId: string;
    amount: number;
}

export class PaymentApiClient {
    constructor(private endpoint: string) {}

    async submit(req: PaymentRequest): Promise<boolean> {
        return true;
    }
}
