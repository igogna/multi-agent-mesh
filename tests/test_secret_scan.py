from tools.secret_scan import scan


def test_detects_aws_key_in_added_line():
    diff = """--- a/config.py
+++ b/config.py
@@ -1,2 +1,3 @@
 import os
+AWS_KEY = "AKIAABCDEFGHIJKLMNOP"
 DEBUG = True
"""
    findings = scan(diff)
    assert len(findings) == 1
    assert "config.py:2" in findings[0]
    assert "AWS access key" in findings[0]


def test_detects_private_key_header():
    diff = """--- a/id_rsa
+++ b/id_rsa
@@ -0,0 +1,1 @@
+-----BEGIN RSA PRIVATE KEY-----
"""
    findings = scan(diff)
    assert len(findings) == 1
    assert "Private key" in findings[0]


def test_ignores_obvious_placeholder_value():
    diff = """--- a/settings.py
+++ b/settings.py
@@ -1,1 +1,2 @@
 import os
+PASSWORD = "changeme"
"""
    assert scan(diff) == []


def test_ignores_secret_only_in_removed_line():
    diff = """--- a/config.py
+++ b/config.py
@@ -1,1 +1,1 @@
-AWS_KEY = "AKIAABCDEFGHIJKLMNOP"
+AWS_KEY = os.environ["AWS_KEY"]
"""
    assert scan(diff) == []


def test_clean_diff_returns_no_findings():
    diff = """--- a/calculator/ops.py
+++ b/calculator/ops.py
@@ -1,2 +1,3 @@
 def add(a, b):
     return a + b
+
"""
    assert scan(diff) == []
