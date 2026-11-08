from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field

# Request bodies for /wallet. Amounts are dollars (e.g. 25 or 25.50), at most two decimal places;
# the minimums and the deposit fee are enforced in app.services.wallet.

class MoneyRequest(BaseModel):
    model_config = ConfigDict(extra = "ignore")

    amount: Decimal = Field(gt = 0, max_digits = 9, decimal_places = 2)
