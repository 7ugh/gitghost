import os, tempfile
os.environ["VIRALFORGE_HOME"] = tempfile.mkdtemp(prefix="vf_test_")
os.environ["VF_FAST_POLL"] = "1"
