class ProductBaseService:
    def __init__(self) -> None:
        pass

    def get_bestseller_products(self):
        raise NotImplementedError

    def get_product_list(self):
        raise NotImplementedError

    def match_product(self, messages: any, option: any = None):
        raise NotImplementedError

    def suggest_product(self, messages: any, option: any = None):
        raise NotImplementedError

    def update_products(self, products_info, option: any = None):
        raise NotImplementedError
