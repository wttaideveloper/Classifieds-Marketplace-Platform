# Product media upload (photos and videos)

For the Product create / edit screens and the product details pages. Single-step upload: the file goes to the API, the answer has the URL to preview and to save on the product.

## `POST /api/v1/products/upload`

* **Who:** Enterprise Admin or Super Admin (bearer token). A Provider or customer gets 403, no token 401.
* **Request:** `multipart/form-data`, one file in the field **`file`** (`formData.append('file', file)`). Nothing else is needed.
* **Limits:** photos (JPEG, PNG, WebP, GIF) up to **10 MB**; videos (MP4, WebM, MOV) up to **50 MB** (`PRODUCT_MAX_IMAGE_MB`, `PRODUCT_MAX_VIDEO_MB`). No SVG.
* **Checks:** the declared type, the file extension and the file's real first bytes must agree. A browser that sends no type (or `application/octet-stream`) is accepted when the extension and contents are valid.

**201 Created**
```json
{ "url": "https://api.example.com/api/v1/products/upload/9f1c2e7a4b6d4c0e8a3b5d7f1e2c4a6b.mp4",
  "name": "Yoga demo.mp4", "size": 18234567, "type": "video/mp4", "kind": "video",
  "stored_name": "9f1c2e7a4b6d4c0e8a3b5d7f1e2c4a6b.mp4" }
```
`kind` is `image` or `video`, so the screen knows whether to render `<img>` or `<video>`. `url` is absolute (`PUBLIC_MEDIA_BASE_URL` / `PUBLIC_API_BASE_URL`; when neither is set, the address the request came in on; https when HTTPS-only media is on).

| HTTP | `detail` |
|---|---|
| 400 | `Uploaded file is empty` |
| 413 | `Photos may be at most 10 MB` / `Videos may be at most 50 MB` |
| 415 | unsupported type; extension does not match the type; `The file's contents are not a real image/jpeg` |
| 422 | no `file` field |
| 401 / 403 | not signed in / not an Enterprise Admin or Super Admin |

## `GET /api/v1/products/upload/{stored_name}`

Public, no token (product pages are public). Served inline with the right media type, `Cache-Control: public, max-age=31536000, immutable`, `X-Content-Type-Options: nosniff`, and **HTTP Range** support (206 partial content), so `<video src>` starts quickly and seeks. 404 for any name an upload did not produce.

## `GET /api/v1/products/upload/policy`

Public. `{ "image": { "mime_types", "extensions", "max_size_bytes" }, "video": { ... } }`, to configure the uploader and check size before sending.

## Saving the media on the product

Nothing about the file is stored in the database: **save the returned `url` in the product** (`image_urls`) when you create or save the product. An upload that no product refers to is deleted after 7 days (`PRODUCT_MEDIA_ORPHAN_DAYS`), checked whenever someone uploads. Deleting a file from a product just means removing its URL from the field.

The product's image field is **one text field with no validation**: it keeps whatever string you send. To keep several photos and videos in it, agree one format on the screens (for example a JSON array of `{url, kind}`) and use it everywhere. There is no separate video field yet.

## Not covered
No thumbnails or video processing, no virus scan, files are on the API server's disk (`UPLOAD_DIR/products`), and removing a URL from a product does not delete the file at once (the clean-up does, after 7 days). Tests: `tests/test_product_media_upload.py` (12, real routes and files, including Range requests).
