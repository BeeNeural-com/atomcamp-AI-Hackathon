import urllib.request
import json
import time

def test_live_pipeline():
    # 1. Fetch Schema ID
    schemas_resp = json.loads(urllib.request.urlopen('http://localhost:8000/api/v1/schemas').read().decode())
    schema_id = schemas_resp['data'][0]['id']
    print("Using Schema ID:", schema_id)

    # 2. Read PDF
    pdf_path = "backend/data/bench/pdf/metro_credit_union_rates_page1__2026-07-01.pdf"
    with open(pdf_path, 'rb') as f:
        pdf_bytes = f.read()

    # 3. Multipart Upload
    boundary = "----TestBoundary12345"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="metro_credit_union_rates_page1__2026-07-01.pdf"\r\n'
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    body = head + pdf_bytes + tail

    req = urllib.request.Request("http://localhost:8000/api/v1/documents", data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    doc_resp = json.loads(urllib.request.urlopen(req).read().decode())
    doc = doc_resp["data"]
    print("Document created:", doc["id"], doc["original_filename"])

    # 4. Create Job
    job_payload = json.dumps({
        "schema_version_id": schema_id,
        "options": {"enable_repair": True, "require_evidence": True},
        "idempotency_key": f"test-{int(time.time())}"
    }).encode("utf-8")

    job_req = urllib.request.Request(
        f"http://localhost:8000/api/v1/documents/{doc['id']}/jobs",
        data=job_payload,
        method="POST",
        headers={"Content-Type": "application/json"}
    )
    job_resp = json.loads(urllib.request.urlopen(job_req).read().decode())
    job_id = job_resp["data"]["job_id"]
    print("Job launched:", job_id, "Status:", job_resp["data"]["status"])

    # 5. Poll Job until complete
    for _ in range(15):
        time.sleep(1)
        j = json.loads(urllib.request.urlopen(f"http://localhost:8000/api/v1/jobs/{job_id}").read().decode())["data"]
        print(f"Polling job {job_id}: status={j['status']}, stage={j.get('stage')}")
        if j["status"] in ("completed", "review_required", "failed"):
            break

    # 6. Fetch Results
    res_resp = json.loads(urllib.request.urlopen(f"http://localhost:8000/api/v1/jobs/{job_id}/results").read().decode())
    records = res_resp["data"]["records"]
    print(f"Extracted {len(records)} records from live document!")
    for r in records[:5]:
        v = r["values"]
        print("  - Product:", v.get("product_type"), "| Term:", v.get("term_months"), "| Rate:", v.get("rate_pct"), "| APY:", v.get("apy_pct"), "| Status:", r["status"])

if __name__ == "__main__":
    test_live_pipeline()
