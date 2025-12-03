import logging
import re
from typing import Optional, Union

logger = logging.getLogger(__name__)


def get_rules(
    request_host: str, cache_rules: dict[str, dict[str, Union[bool, int]]]
) -> Optional[dict[str, Union[bool, int]]]:
    for site_pattern, rules in cache_rules.items():
        if re.match(site_pattern, request_host):
            logger.info("matched %s, using value %s: %s", site_pattern, request_host, rules)

            return rules

    logger.debug("No patterns matched %s", request_host)


def match_request(target: str, cache_rules_for_site: dict[str, Union[bool, int]]):
    for pat, v in cache_rules_for_site.items():
        if re.match(pat, target):
            logger.info("%s matched %s, using value %s", target, pat, v)

            return v


def get_rule_for_request(
    request_host: str, target: str, cache_rules: dict[str, dict[str, Union[bool, int]]]
) -> Optional[Union[bool, int]]:
    cache_rules_for_site = get_rules(request_host=request_host, cache_rules=cache_rules)

    if cache_rules_for_site:
        is_cacheable = match_request(target=target, cache_rules_for_site=cache_rules_for_site)
        return is_cacheable

    return None

