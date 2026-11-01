from . import bill_vs_po, expired_lots, moves_without_entries, negative_stock, stock_vs_ledger

ALL_CHECKS = {
    module.NAME: module
    for module in (stock_vs_ledger, moves_without_entries, bill_vs_po, expired_lots, negative_stock)
}
