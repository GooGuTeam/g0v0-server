BANCHOBOT_ID = 3

BACKUP_CODE_LENGTH = 10

NEW_SCORE_FORMAT_VER = 20220705
SUPPORT_TOTP_VERIFICATION_VER = 20250913

# Cloudflare's published edge ranges (https://www.cloudflare.com/ips/).
# A Cloudflare edge hop is treated as a trusted proxy so that CF-Connecting-IP,
# which the edge always sets and clients cannot forge, wins over the
# client-controlled leading X-Forwarded-For entries.
CLOUDFLARE_PROXY_NETWORKS = (
    "173.245.48.0/20",
    "103.21.244.0/22",
    "103.22.200.0/22",
    "103.31.4.0/22",
    "141.101.64.0/18",
    "108.162.192.0/18",
    "190.93.240.0/20",
    "188.114.96.0/20",
    "197.234.240.0/22",
    "198.41.128.0/17",
    "162.158.0.0/15",
    "104.16.0.0/13",
    "104.24.0.0/14",
    "172.64.0.0/13",
    "131.0.72.0/22",
    "2400:cb00::/32",
    "2606:4700::/32",
    "2803:f800::/32",
    "2405:b500::/32",
    "2405:8100::/32",
    "2a06:98c0::/29",
    "2c0f:f248::/32",
)

# Maximum score in standardised scoring mode
# https://github.com/ppy/osu/blob/master/osu.Game/Rulesets/Scoring/ScoreProcessor.cs
MAX_SCORE = 1000000
