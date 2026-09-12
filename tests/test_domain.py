import pytest
from pydantic import ValidationError

from accuflow.domain.models import StockCreate


def test_stock_symbol_is_normalized():
    assert StockCreate(symbol=" brk.b ").symbol == "BRK.B"


@pytest.mark.parametrize("symbol", ["", "AAPL!", "TOO-LONG-SYMBOL"])
def test_invalid_stock_symbol_is_rejected(symbol):
    with pytest.raises(ValidationError):
        StockCreate(symbol=symbol)

