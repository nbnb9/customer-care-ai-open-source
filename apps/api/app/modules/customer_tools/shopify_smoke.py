from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .errors import CustomerToolsProviderError
from .factory import get_customer_tools_adapter
from .shopify_adapter import ShopifyCustomerToolsAdapter


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the read-only Shopify Dev Store contract smoke test."
    )
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--customer-id", required=True)
    parser.add_argument("--order-id", required=True)
    parser.add_argument("--order-number", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        adapter = get_customer_tools_adapter()
        if not isinstance(adapter, ShopifyCustomerToolsAdapter):
            raise CustomerToolsProviderError("Shopify sandbox provider is not selected")
        order = adapter.query_order_status(
            tenant_id=args.tenant_id,
            verified_customer_id=args.customer_id,
            candidate_order_number=args.order_number,
        )
        warranty = adapter.query_warranty_eligibility(
            tenant_id=args.tenant_id,
            verified_customer_id=args.customer_id,
            candidate_order_number=args.order_number,
        )
        policy_facts = adapter.lookup_owned_order_facts(
            tenant_id=args.tenant_id,
            customer_id=args.customer_id,
            order_id=args.order_id,
        )
    except CustomerToolsProviderError:
        print(
            json.dumps(
                {"status": "failed", "reason": "provider_unavailable"},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    if order is None or warranty is None or policy_facts is None:
        print(
            json.dumps(
                {"status": "failed", "reason": "not_found_or_not_owned"},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 3

    result = {
        **adapter.diagnostic_summary(),
        "status": "passed",
        "order_status": order.status,
        "policy_facts_source": policy_facts.source_system,
        "warranty_eligible": warranty.eligible,
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
