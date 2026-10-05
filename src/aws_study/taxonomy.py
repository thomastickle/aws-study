from __future__ import annotations

import re

# Intentionally conservative. These labels are study aids, not official AWS exam-domain mappings.
RULES: list[tuple[str, list[str]]] = [
    ("Hybrid storage", ["storage gateway", "hybrid cloud storage"]),
    ("Migration", ["migration service", "application migration", "aws mgn", "database migration", "dms", "schema conversion", "sct"]),
    ("Hybrid networking", ["direct connect", "site-to-site vpn", "client vpn", "on-premises", "on premises"]),
    ("EC2 purchasing & tenancy", ["spot instances", "reserved instances", "on-demand", "dedicated host", "dedicated instances", "capacity reservation"]),
    ("Identity & access", ["iam", "identity and access management", "permissions", "security group"]),
    ("Security services", ["guardduty", "macie", "inspector", "security hub", "trusted advisor", "waf", "shield"]),
    ("S3 & object storage", ["amazon s3", "s3 bucket", "bucket versioning", "lifecycle", "access point"]),
    ("Observability & audit", ["cloudwatch", "cloudtrail", "aws config", "audit", "metrics", "logs"]),
    ("Governance & frameworks", ["cloud adoption framework", "aws caf", "well-architected", "well architected", "governance perspective"]),
    ("Cost management", ["cost explorer", "budgets", "billing", "cost and usage", "rightsizing", "optimize for cost"]),
    ("Databases & analytics", ["aurora", "rds", "redshift", "athena", "emr", "dynamodb", "database"]),
    ("Global infrastructure", ["availability zone", "availability zones", "region", "global reach", "edge location", "cloudfront"]),
    ("Support & recommendations", ["support plan", "enterprise support", "business support", "developer support", "trusted advisor"]),
]

SERVICE_PATTERN = re.compile(
    r"\b(?:AWS|Amazon)\s+(?:[A-Z][A-Za-z0-9-]*(?:\s+[A-Z][A-Za-z0-9-]*){0,4})\b"
)


def classify(question: str, option_texts: list[str]) -> tuple[str, str, list[str]]:
    blob = (question + " " + " ".join(option_texts)).lower()
    topic = "General AWS"
    for label, needles in RULES:
        if any(n in blob for n in needles):
            topic = label
            break

    services = []
    seen = set()
    for opt in option_texts:
        cleaned = " ".join(opt.split()).strip().rstrip(".,:;)")
        if (cleaned.startswith("AWS ") or cleaned.startswith("Amazon ")) and len(cleaned) <= 80:
            if cleaned not in seen:
                seen.add(cleaned)
                services.append(cleaned)
    tags = [topic.lower().replace(" & ", "-").replace(" ", "-")]
    tags.extend(s.lower().replace(" ", "-") for s in services[:6])
    concept = " vs ".join(services[:4]) if 1 < len(services) <= 4 else topic
    return topic, concept, tags
