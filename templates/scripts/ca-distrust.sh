#!/bin/sh
# Remove trust in CA roots distrusted after a compromise, then fail if any
# trust store in the image still has them. First case: eMudhra (2026-10-17),
# whose intermediates' private keys can be derived from the public certificates.
# The distro fix drops "emSign ECC Root CA - G3" and "AAA Certificate Services",
# but base images lag behind (eclipse-temurin:*-jre-jammy still shipped both in
# its system bundle and its own cacerts on 2026-10-22), so a rebuild with
# --pull alone does not fix them. Matched by SHA-256: other emSign roots stay.
#
#   1. upgrade ca-certificates with the image's package manager
#   2. system store: '!' in /etc/ca-certificates.conf (Debian, Ubuntu, Alpine)
#      or the p11-kit blocklist (RHEL, Amazon Linux)
#   3. keytool -delete from every Java cacerts that still has them
#   4. verify: system bundle, Java cacerts, certifi and Node
set -u
BAD="86A1ECBA089C4A8D3BBE2734C612BA341D813E043CF9E8A862CD5C57A36BBE6B D7A7A0FB5D7E2731D771E9484EBCDEF71D5F0C3E0A2948782BC83EE0EA699EF4"
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT

is_bad() { case " $BAD " in *" $1 "*) return 0 ;; esac; return 1; }
fp_of() { openssl x509 -in "$1" -noout -fingerprint -sha256 2>/dev/null | sed 's/.*=//; s/://g'; }
split_pem() { # file prefix
  rm -f "$T/$2"*.pem
  awk -v d="$T/$2" '/BEGIN CERT/{n++} n{print > (d n ".pem")}' "$1"
}

# --- 1. package ------------------------------------------------------------
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -q >/dev/null 2>&1
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends ca-certificates openssl >/dev/null
  rm -rf /var/lib/apt/lists/*
elif command -v apk >/dev/null 2>&1; then
  apk add --no-cache -q -u ca-certificates openssl >/dev/null
elif command -v microdnf >/dev/null 2>&1; then
  microdnf install -y ca-certificates openssl >/dev/null 2>&1; microdnf update -y ca-certificates >/dev/null 2>&1
elif command -v yum >/dev/null 2>&1; then
  yum install -y -q ca-certificates openssl >/dev/null 2>&1; yum update -y -q ca-certificates >/dev/null 2>&1
fi
command -v openssl >/dev/null 2>&1 || { echo "ca-distrust: no openssl" >&2; exit 1; }

# --- 2. system store -------------------------------------------------------
if [ -f /etc/ca-certificates.conf ] && [ -d /usr/share/ca-certificates ]; then
  changed=0
  for f in $(find /usr/share/ca-certificates -name '*.crt'); do
    is_bad "$(fp_of "$f")" || continue
    rel=${f#/usr/share/ca-certificates/}
    grep -qxF "!$rel" /etc/ca-certificates.conf && continue
    grep -vxF "$rel" /etc/ca-certificates.conf > "$T/conf"
    echo "!$rel" >> "$T/conf"
    cat "$T/conf" > /etc/ca-certificates.conf
    changed=1
  done
  [ $changed -eq 1 ] && update-ca-certificates >/dev/null 2>&1
elif [ -d /etc/pki/ca-trust/source ]; then
  mkdir -p /etc/pki/ca-trust/source/blocklist
  b=$(ls /etc/pki/tls/certs/ca-bundle.crt /etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem 2>/dev/null | head -1)
  if [ -n "$b" ]; then
    split_pem "$b" s
    for f in "$T"/s*.pem; do
      [ -f "$f" ] || continue
      fp=$(fp_of "$f")
      is_bad "$fp" && cp "$f" "/etc/pki/ca-trust/source/blocklist/distrust-$fp.pem"
    done
    update-ca-trust extract
  fi
fi

# --- 3. Java cacerts -------------------------------------------------------
kt=$(command -v keytool 2>/dev/null || ls "${JAVA_HOME:-/nonexistent}/bin/keytool" /opt/java/openjdk/bin/keytool /usr/lib/jvm/*/bin/keytool 2>/dev/null | head -1)
stores=""
for c in "${JAVA_HOME:-/nonexistent}/lib/security/cacerts" "${JAVA_HOME:-/nonexistent}/jre/lib/security/cacerts" \
         /opt/java/openjdk/lib/security/cacerts /etc/ssl/certs/java/cacerts /etc/pki/java/cacerts \
         /usr/lib/jvm/*/lib/security/cacerts /usr/lib/jvm/*/jre/lib/security/cacerts; do
  [ -f "$c" ] || continue
  r=$(readlink -f "$c")
  case " $stores " in *" $r "*) continue ;; esac
  stores="$stores $r"
done
for s in $stores; do
  [ -n "$kt" ] || { echo "ca-distrust: no keytool for $s" >&2; exit 1; }
  LC_ALL=C "$kt" -list -v -keystore "$s" -storepass changeit 2>/dev/null | awk '
    /^Alias name: /{a=substr($0, 13)}
    /SHA256: /{f=$0; sub(/.*SHA256: */, "", f); gsub(/[:\r]/, "", f); print a "|" f}' > "$T/aliases"
  while IFS='|' read -r alias fp; do
    is_bad "$fp" && LC_ALL=C "$kt" -delete -noprompt -alias "$alias" -keystore "$s" -storepass changeit >/dev/null 2>&1
  done < "$T/aliases"
done

# --- 4. verify -------------------------------------------------------------
found=""
pem_check() { # name file
  split_pem "$2" v
  for f in "$T"/v*.pem; do
    [ -f "$f" ] || continue
    fp=$(fp_of "$f"); is_bad "$fp" && found="$found $1:$(echo "$fp" | cut -c1-8)"
  done
}
for b in /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt /etc/ssl/cert.pem; do
  [ -s "$b" ] && { pem_check "$b" "$b"; break; }
done
for py in python3 python; do
  cb=$($py -m certifi 2>/dev/null) || continue
  [ -s "$cb" ] && pem_check "certifi" "$cb"
  break
done
if command -v node >/dev/null 2>&1; then
  node -e 'console.log(require("tls").rootCertificates.join("\n"))' > "$T/node.pem" 2>/dev/null
  [ -s "$T/node.pem" ] && pem_check "node" "$T/node.pem"
fi
for s in $stores; do
  LC_ALL=C "$kt" -list -v -keystore "$s" -storepass changeit 2>/dev/null | sed -n 's/.*SHA256: *//p' | tr -d ':\r' > "$T/fps"
  while read -r fp; do is_bad "$fp" && found="$found $s:$(echo "$fp" | cut -c1-8)"; done < "$T/fps"
done
if [ -n "$found" ]; then
  echo "ca-distrust: distrusted roots still trusted:$found" >&2
  exit 1
fi
echo "ca-distrust: OK"
