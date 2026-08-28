# Frontend Handoff — Classifieds Marketplace Platform

**Base URL (prod):** `https://chat.wisdomtooth.tech/api` → all paths prefix `/api/v1`
**OpenAPI JSON:** `docs/openapi.json` (live copy, also `GET https://chat.wisdomtooth.tech/api/openapi.json`, Swagger `https://chat.wisdomtooth.tech/api/docs`)
**Postman (legacy):** `postman/Classifieds-Marketplace-Platform.postman_collection.json` (covers Enterprise/Products/Services; Events/Trainings/Programs use same `{{baseUrl}}`)
**Auth:** Keycloak RS256 `https://auth.invigor8.app/realms/invigorate-healthcare` → `POST https://admin.apis.invigor8.app/api/v1/auth/login` → `tokens.access_token`. Send `Authorization: Bearer <token>` or `WEB_SESSION_COOKIE`. Audience `invigorate-api`. Dev: `GET/POST /api/v1/auth/dev-token` when `ENABLE_DEV_TOKEN=true`. `GET /api/v1/auth/session` for frontend auth check. `GET /api/v1/auth/integration` returns issuer/audience/JWKS/roles.
**Roles:** `admin` / `provider` via `require_roles(["admin","provider"])`. Only `status == "approved"` needs admin now (`e711778`: `published/draft/archived/completed/suspended` allowed for provider after first approval). `get_current_admin` needs `role=="admin"`.
**Pagination:** `?page=1&page_size=20` (`DEFAULT_PAGE/PAGE_SIZE/MAX_PAGE_SIZE`), response `{items:[], pagination:{total,page,page_size,total_pages}}`.
**Errors:** `400 Cannot transition from 'x' to 'y'. Allowed: [...]` (VALID_TRANSITIONS `event_service.py:246` / `training_service.py:57` / `program_service.py:43`), `400 Enterprise not approved (status=draft/pending/inactive)`, `400 Registration closed / at capacity / Event is cancelled`, `403 Only admin can approve`, `404 Not found`.

---

## Events — 46 ops `Events` tag

| Method | Path | Summary | Auth | Request Body | 200 Response | Frontend Usage |
|---|---|---|---|---|---|---|
| POST | `/api/v1/events/` | Create Event | `admin,provider` | `EventCreate` (`enterprise_id*`, `title*`, `category*`, `start_date*`, `end_date*`, `duration_type:one_day|half_day|custom`, `time_zone:Asia/Kolkata`, `registration_cutoff/open_at/close_at`, `primary_image`, `gallery_images[]`, `videos[]`, `documents[]`, `delivery_mode:in_person|online|hybrid`, `venue:{address,city,lat,lng,instructions,map_url}`, `meeting_link`, `meeting_provider:zoom|google_meet|teams|other`, `price/currency:INR`, `ticket_types:[{id,name,price,currency,capacity,early_bird_price,early_bird_until,promo_price}]`, `capacity/min/max_participants`, `custom_fields[]`, `sessions:[{id,session_date,start_time,end_time,title,speaker,location,meeting_link}]`, `status:draft` ) | `201 EventResponse {id,enterprise_id,title,category,status:draft, ...}` | Create form; enterprise must be `active` else `400` |
| GET | `/api/v1/events/` | List Events | public | `?search=&category=&tenant_id=&enterprise_id=&location_id=&status=&delivery_mode=&date_from=YYYY-MM-DD&date_to=&min_price=&max_price=&page=&page_size=` | `EventPaginatedResponse` | Search/filter chips + pagination; date/price filters |
| GET | `/api/v1/events/my/registrations` | My registrations | `get_current_user` (email from token) | `?status=confirmed|attended|cancelled` | `[{registration_id,event_id,event_title,event_status,event_start,registration_status,qr_code,checked_in_at}]` | Participant dashboard upcoming/completed/cancelled |
| GET | `/api/v1/events/{event_id}` | Get Event by ID | public | — | `EventDetailResponse` (organiser, sessions, availability `{available_seats,is_full,registration_open}`) | Detail page: organiser/schedule/availability |
| PUT | `/api/v1/events/{event_id}` | Update Event | `admin,provider` | `EventUpdate` (partial of create) | `200 EventResponse` | Edit form |
| DELETE | `/api/v1/events/{event_id}` | Delete Event | `admin,provider` | — | `{"message":"Event deleted successfully"}` | Soft delete `is_deleted` |
| POST | `/api/v1/events/{event_id}/duplicate` | Duplicate Event | `admin,provider` | — | `201 EventResponse` new `id`, `status:draft`, sessions re-uuid'd | Duplicate button |
| PATCH | `/api/v1/events/{event_id}/status` | Update Event Status | `admin,provider` (`approved` admin only) | `EventStatusUpdate {status:draft|pending_approval|approved|published|cancelled|completed|suspended|archived, reason?}` | `200 EventResponse` | Status dropdown; VALID `pending→approved→draft→published→completed→archived` etc; `403` if provider tries `approved` |
| POST | `/api/v1/events/{event_id}/unpublish` | Unpublish Event | `admin,provider` | — | `200 EventResponse status:approved` | Unpublish → keeps approval |
| POST | `/api/v1/events/{event_id}/archive` | Archive Event | `admin,provider` (now provider allowed) | — | `200 EventResponse status:archived` | Archive; allowed `draft/approved/suspended/cancelled/completed→archived`, not `pending_approval/published` directly |
| GET | `/api/v1/events/{event_id}/registrations` | List Registrations | `admin,provider` | — | `[{id,participant_name,email,status,qr_code,ticket_type_id,checked_in_at}]` | Participant list |
| POST | `/api/v1/events/{event_id}/registrations` | Register for Event | `get_current_user` | `EventRegistrationCreate {participant_name,email,ticket_type_id?,group_size?,group_members:[{name,email}],custom_fields:{}}` | `201 {id,qr_code,status:confirmed}` | Instant free; `400 at capacity / window closed / event is cancelled/completed → join waitlist` |
| DELETE | `/api/v1/events/{event_id}/registrations/{reg_id}` | Cancel Registration | `get_current_user` (owner) | — | `{"message":"Registration cancelled"}` + `notify_single_cancellation` | Cancel button |
| GET | `/api/v1/events/{event_id}/registrations/export` | Export Registrations CSV | `admin,provider` | — | `text/csv` `id,name,email,status,qr_code` | Download CSV |
| POST | `/api/v1/events/{event_id}/checkout` | Checkout — Paid Registration | `get_current_user` | `EventCheckoutRequest {participant_name,email,ticket_type_id,quantity:1,payment_provider:marketplace|merchant}` | `201 EventOrderResponse {id,event_id,quantity,amount,currency,payment_status, status}` | Paid flow; `is_free` stub `confirmed` |
| GET | `/api/v1/events/{event_id}/orders` | List Orders | `admin,provider` | — | `[EventOrder]` | Order tracking |
| POST | `/api/v1/events/{event_id}/registrations/{reg_id}/refund` | Request Refund | `get_current_user` | `EventRefundRequest {reason?}` | `EventOrder payment_status:refund_requested` | Refund button; `403` if `attended` |
| POST | `/api/v1/events/{event_id}/orders/{order_id}/refund` | Refund Order | `admin,provider` | `EventRefundRequest` | same | — |
| GET/POST/DELETE | `/api/v1/events/{event_id}/waitlist` + `/{entry_id}` | Waitlist | `get_current_user` / `admin,provider` for list | `EventRegistrationCreate` for join | `201 WaitlistEntry` / list / `{"message":"Removed"}` | Join when `400 Please join waitlist` |
| GET/POST/PUT/DELETE | `/api/v1/events/{event_id}/sessions` `/{session_id}` | Sessions CRUD | `get_current_user` for list, `admin,provider` for write | `EventSessionCreate {session_date,start_time,end_time,title,speaker,location,meeting_link}` | `201/200` | Agenda editor; sorted by `session_date,start_time` |
| POST | `/api/v1/events/{event_id}/check-in` | Check-in Participant | `admin,provider` | `EventCheckInRequest {registration_id?|qr_code?,session_id?}` | `200 EventCheckInResponse {status:attended,checked_in_at,session_id}` idempotent `Already checked in` | Scanner / manual |
| POST | `/api/v1/events/{event_id}/uncheck-in` | Undo Check-in | `admin,provider` | `EventUncheckInRequest` | `200 {status:confirmed,checked_in_at:null}` | Undo |
| POST | `/api/v1/events/{event_id}/check-out` | Check-out Participant | `admin,provider` | `EventCheckOutRequest` | `200 checked_out_at` | — |
| POST | `/api/v1/events/{event_id}/validate-qr` | Validate QR Code | `admin,provider` | `{qr_code}` | `EventQRValidateResponse {valid,registration_id,participant_name,status}` | Scanner |
| GET | `/api/v1/events/{event_id}/registrations/{reg_id}/qr` | Get QR Code Image | `get_current_user` | — | `image/png` `qrcode` or `{qr_code}` fallback | Participant QR |
| GET | `/api/v1/events/{event_id}/calendar.ics` | Add to calendar — Event + Sessions (ICS) | public | — | `text/calendar` `VCALENDAR` with `VEVENT`s | Add to calendar button (Google/Outlook/365) |
| GET | `/api/v1/events/{event_id}/sessions/{session_id}/calendar.ics` | Single Session ICS | public | — | `text/calendar` | Per-session calendar |
| GET | `/api/v1/events/{event_id}/meeting-link` | Get Meeting Link (registered only) | `get_current_user` (403 if not `confirmed|attended` unless admin) | — | `{meeting_link,meeting_provider,delivery_mode}` | Show after registration |
| POST | `/api/v1/events/{event_id}/contact` | Contact Organiser | `get_current_user` | `{message}` | `{"message":"Message sent to organiser"}` ` _safe_notify` | Contact button |
| GET | `/api/v1/events/{event_id}/attendance` | Attendance Report | `admin,provider` | — | `{total,attended,confirmed,no_show}` | Attendance tab |
| POST | `/api/v1/events/{event_id}/announcements` | Send Announcement | `admin,provider` | `EventAnnouncementCreate {title,message,channel:in_app|email|sms|both}` | `201 {id,title,message,sent_at}` fan-out `in_app+push+email+sms` | Announce to `confirmed|attended` |
| POST | `/api/v1/events/{event_id}/remind` | Send Event Reminder | `admin,provider` | — | `{"message":"Reminders sent"}` fan-out | Reminder button |
| POST | `/api/v1/events/{event_id}/feedback` | Submit Feedback | `get_current_user` | `{rating?,comment, ...}` `is_review=false` | `201 Feedback` | Post-event form |
| GET | `/api/v1/events/{event_id}/feedback` | List Feedback | `admin,provider` | — | `[Feedback]` | Admin moderate |
| POST | `/api/v1/events/{event_id}/reviews` | Submit Review | `get_current_user` (must be `confirmed|attended` verified) | `{rating 1-5,comment,participant_email}` | `201 Review verified:true` | Verified review; `400` if not enrolled |
| PATCH | `/api/v1/events/{event_id}/reviews/{review_id}/moderate` | Moderate Review | `admin` only | `{action:approved|rejected}` | `200 Review` | Moderation queue |
| GET | `/api/v1/events/{event_id}/reports` | Event Reports | `admin,provider` | `?type=registration|attendance|revenue|cancellation|completion&format=json` | `{type,data:{total,by_status,...}}` | Reports tab |
| GET | `/api/v1/events/reports/summary` | Performance Dashboard | `admin,provider` | `?enterprise_id=` | `{total_events,by_status,by_category,by_delivery_mode,total_registrations}` | Dashboard |
| GET/POST | `/api/v1/events/templates` | Templates | `get_current_user` / `admin,provider` | `{template_data}` | `[Template]` / `201 Template` | Reusable templates |
| POST | `/api/v1/events/templates/{template_id}/apply` | Apply Template | `admin,provider` | `{enterprise_id}` | `201 Event status:draft` with new session ids | Apply template |

---

## Trainings — 49 ops

| Method | Path | Summary | Auth | Request/Notes |
|---|---|---|---|---|
| POST | `/api/v1/trainings/` | Create Training | `admin,provider` | `TrainingCreate: enterprise_id*,title*,category*,subcategory,tags[],instructor_id,requirements,primary_image,gallery_images[],promotional_video,documents[],delivery_mode:self_paced|instructor_led|blended,course_type:one_day|workshop|virtual|certification,duration, start_date/end_date/enrolment_start/end_date, time_zone:Asia/Kolkata, capacity,price/currency/ promo_price/coupon_code, requires_approval,bool, access_duration_days, status:draft` — enterprise must be `active` `training_service.py:11` |
| GET | `/api/v1/trainings/` | List Trainings | public | `?search=&category=&provider=&tenant_id=&enterprise_id=&location_id=&status=&delivery_mode=&min_price=&max_price=&duration=&date_from=&date_to=&page=&page_size=` (`provider`=`instructor_id`) via `1bf6be0` filters |
| GET | `/api/v1/trainings/{training_id}` | Get Training | `get_current_user` | `TrainingDetailResponse` |
| PUT/DELETE | `/api/v1/trainings/{training_id}` | Update/Delete | `admin,provider` | — |
| POST | `/api/v1/trainings/{training_id}/duplicate` | Duplicate | `admin,provider` | `status:draft` clone |
| PATCH | `/api/v1/trainings/{training_id}/status` | Update Status | `admin,provider` (`approved` admin only) | `VALID same as events` `57` |
| POST | `/api/v1/trainings/{training_id}/unpublish` | Unpublish → `approved` | `admin,provider` | — |
| POST | `/api/v1/trainings/{training_id}/archive` | Archive → `archived` | `admin,provider` (now provider allowed `e711778`) | — |
| GET/POST | `/api/v1/trainings/{training_id}/sections` | Builder sections (=modules) `SectionCreate {title,type:section|module,order,instructor_id,schedule}` | `get_current_user` / `admin,provider` | `Training.sections JSONB` |
| POST | `/api/v1/trainings/{training_id}/sections/reorder` + `/modules/reorder` alias | Reorder modules | `admin,provider` | `{ordered_ids:[]}` |
| POST | `/api/v1/trainings/{training_id}/sections/{section_id}/lessons/reorder` | Reorder lessons | `admin,provider` | `{ordered_ids:[]}` `7d8e0b4` |
| POST | `/api/v1/trainings/{training_id}/sections/{section_id}/lessons` | Add Lesson | `admin,provider` | `LessonCreate {type:text|video|audio|webpage|pdf|presentation|worksheet|document|live, title,content_url,topics:[], is_preview,is_draft,is_mandatory,completion_rule,prerequisites:[],release_rule:{mode:date|enrolment_day|previous_lesson},instructor_id}` `d71c420` |
| PUT/DELETE | `/api/v1/trainings/{training_id}/sections/{section_id}/lessons/{lesson_id}` | Update/Delete Lesson | `admin,provider` | — |
| GET | `/api/v1/trainings/my/enrolments` | Participant dashboard (my enrolments) | `get_current_user` | `?status=enrolled|pending_approval|cancelled|waitlisted` |
| POST | `/api/v1/trainings/{training_id}/enrol` | Enrol | `get_current_user` | `{participant_name,email,group_enrol?,group_members:[],coupon_code?}` instant or `pending_approval` if `requires_approval` + `access_expires_at` |
| GET | `/api/v1/trainings/{training_id}/enrolments` | List Enrolments | `admin,provider` | — |
| GET | `/api/v1/trainings/{training_id}/content` | Secure enrolled content — gated | `get_current_user` (403 if not enrolled nor admin) | `{sections,assessments}` |
| DELETE | `/api/v1/trainings/{training_id}/enrolments/{enrol_id}` | Cancel enrolment | `get_current_user` (own) or `admin,provider` any | `cancel_training_enrol_service` → `cancelled` |
| POST | `/api/v1/trainings/{training_id}/enrolments/{enrol_id}/approve` | Approve/Reject Enrolment | `admin,provider` | `{action:approve|reject}` → `enrolled|cancelled` |
| POST | `/api/v1/trainings/{training_id}/checkout` | Checkout — Training | `get_current_user` | `TrainingCheckoutRequest {participant_name,email,quantity,coupon_code,payment_provider:marketplace|merchant}` → `TrainingOrder` `confirmed` |
| GET | `/api/v1/trainings/{training_id}/orders` | List Training Orders | `admin,provider` | — |
| POST/DELETE | `/api/v1/trainings/{training_id}/waitlist` | Waitlist | `get_current_user` | `2023` `TrainingWaitlist` |
| POST | `/api/v1/trainings/{training_id}/assessments` | Create quizzes/tests/surveys | `admin,provider` | `{title,level:pre-course|module|final|feedback, passing_score,pass_mark, attempt_limit,time_limit_minutes, publication:immediate|scheduled, publish_at, randomize, questions:[]}` `d243640` |
| GET | `/api/v1/trainings/{training_id}/assessments` | List Assessments | `get_current_user` | `?randomize=true` shuffles `questions`+`options` |
| GET | `/api/v1/trainings/{training_id}/question-bank` | Question bank | `admin,provider` | `reusable:true` aggregated |
| POST | `/api/v1/trainings/{training_id}/assessments/{aid}/questions` | Add Question | `admin,provider` | `AssessmentQuestionCreate {question_text,question_type:mcq|multiple_select|true_false|short_answer|essay, options[],correct_answer,points,explanation,reusable}` |
| POST | `/api/v1/trainings/{training_id}/assessments/{aid}/submit` | Submit — auto scoring | `get_current_user` | `AssessmentSubmitCreate {answers:[{question_id,answer}]}` → `403 access expired`, `400 attempt limit`, scheduled `publish_at` block, `multiple_select` comma-set compare |
| POST | `/api/v1/trainings/{training_id}/assessments/{aid}/submissions/{sid}/grade` | Manual evaluation | `admin,provider` | `{score|grade,feedback}` → `grade_assessment_manual_service` |
| GET | `/api/v1/trainings/{training_id}/assessments/{aid}/submissions/{sid}/review` | Answer explanations & result review | `get_current_user` | `{submission_id,score,passed,review:[{question_text,given,correct,explanation}]}` |
| POST | `/api/v1/trainings/{training_id}/assignments` | Create Assignment | `admin,provider` | `AssignmentCreate {title,type:assignment|task|practical,instructions,due_date,max_score,accepted_file_types,allow_late_submissions}` `d71c420` |
| POST | `/api/v1/trainings/{training_id}/assignments/{aid}/submit` | Submit text/links/images/videos/docs | `get_current_user` | `AssignmentSubmitCreate {file_url,submission_text}` resubmission allowed (new row) `403 access expired` |
| POST | `/api/v1/trainings/{training_id}/assignments/{aid}/submissions/{sid}/grade` | Instructor feedback & grading | `admin,provider` | `{grade,feedback}` → `grade_assignment_service` |
| POST | `/api/v1/trainings/{training_id}/progress/complete-lesson` | Track lesson/module progress & resume | `get_current_user` | `{lesson_id,participant_email?}` → `TrainingProgress lessons_completed`, `overall_percent`, `mandatory_done`, `completed_at` + `certificate_url` when 100% or mandatory done, `resume_lesson` |
| POST | `/api/v1/trainings/{training_id}/live-sessions/{session_id}/attendance` | Track attendance for live sessions | `get_current_user` | `{participant_email}` → `live:session_id` in `lessons_completed` |
| GET | `/api/v1/trainings/{training_id}/certificate` | Digital completion certificate | public (email param) | `?participant_email=` → `{certificate_url,completed_at,overall_percent}` `400` if not complete |
| GET | `/api/v1/trainings/{training_id}/progress` | Progress | `get_current_user` | `?participant_email` → `{overall_percent,sections_done,lessons_done,certificate_url,expired,access_expires_at}` `403` on submit if expired |
| POST/GET | `/api/v1/trainings/{training_id}/live-sessions` | Live sessions Zoom/Meet/Teams | `admin,provider` / `get_current_user` | `TrainingLiveSessionCreate {title,description,scheduled_at,duration_minutes,meeting_link,meeting_provider:zoom|teams|meet|other}` |
| GET | `/api/v1/trainings/{training_id}/calendar.ics` | Calendar integration ICS | public | `text/calendar` via `calendar_service` |
| GET | `/api/v1/trainings/{training_id}/meeting-link` | Secure meeting link (enrolled only) | `get_current_user` 403 if not enrolled | `[{session_id,title,meeting_link,provider}]` |
| GET/POST | `/api/v1/trainings/{training_id}/discussions` | Discussion Q&A | `get_current_user` | `POST {question|text}` → `{id,author,question,created_at}` |
| POST | `/api/v1/trainings/{training_id}/announcements` | Announcements | `admin,provider` | `AnnouncementCreate {title,message,channel:in_app|email|sms|both}` |

---

## Programs — 38 ops

| Method | Path | Summary | Auth | Notes |
|---|---|---|---|---|
| POST/GET/PUT/DELETE | `/api/v1/programs/` `/{program_id}` | Create/List/Get/Update/Delete/Duplicate | `admin,provider` / public list `?search=&category=&provider=&tenant_id=&enterprise_id=&location_id=&status=&delivery_mode=&min_price=&max_price=&duration=&date_from=&date_to=` `1bf6be0` min/max via `cast Float`, `provider`→`provider_id` | `ProgramCreate: enterprise_id*,title*,category*,provider_id,duration_weeks,eligibility,start_date/end_date,enrolment_start/end,enrol_type:fixed|enrol_anytime,delivery_mode:offline|online|hybrid,price/currency,capacity,status:draft` + duplicate `status:draft` |
| PATCH | `/api/v1/programs/{program_id}/status` | Update Status | `admin,provider` (`approved` admin only) | `VALID same` `program_service.py:43` |
| GET/POST/PUT/DELETE | `/api/v1/programs/{program_id}/phases` `/{phase_id}` | Phases CRUD (=stages/weeks/days/milestones) `PhaseCreate {title,type:phase,phase_type:phase|stage|week|day|milestone,order,prerequisites:[],completion_rule,release_schedule:{mode:daily|weekly|milestone},goals,baseline,expected_outcomes,instructors:[]}` | `get_current_user` list / `admin,provider` write | `Program.phases JSONB` `d21a70f` `extra="allow"` |
| POST/PUT/DELETE | `/api/v1/programs/{program_id}/phases/{phase_id}/activities` `/{activity_id}` | Activities CRUD `ActivityCreate {type:lesson|appointment|task|assessment|live_session|document|video|webpage,title,content_url,resource_url,session_type:individual|group,release,prerequisites}` | `admin,provider` | `activities[]` in `phases` |
| PUT | `/api/v1/programs/{program_id}/phases/{phase_id}/instructors` | Assign instructors/coaches/mentors/service providers | `admin,provider` | `{instructors:[],coaches:[],mentors:[],service_providers:[]}` → `phases` |
| POST | `/api/v1/programs/{program_id}/check-ins/{checkin_id}/feedback` | Provider feedback & progress notes | `admin,provider` | `{feedback}` appends `[Provider:email] feedback` to `notes` |
| POST | `/api/v1/programs/{program_id}/phases/reorder` | Reorder Phases | `admin,provider` | `{ordered_ids:[]}` |
| GET | `/api/v1/programs/my/enrolments` | My enrolments (participant dashboard) | `get_current_user` | `?status=enrolled|completed|cancelled` |
| POST | `/api/v1/programs/{program_id}/enrol` | Enrol — fixed-date & enrol-anytime | `get_current_user` | `EnrolmentCreate {participant_name,email,group_enrol,goals,baseline,expected_outcomes}` `3a9cafa` enforces `enrol_type` window + `400 at capacity → join waitlist` |
| GET | `/api/v1/programs/{program_id}/enrolments` | List Enrolments | `admin,provider` | — |
| POST/GET | `/api/v1/programs/{program_id}/waitlist` | Waitlist | `get_current_user` / `admin,provider` | `status:waitlisted`, `POST /waitlist` when `available==0` |
| GET | `/api/v1/programs/{program_id}/availability` | Available seats & waitlist | public | `{capacity,enrolled,available_seats,is_full,waitlist_count,enrol_type,delivery_mode,is_free}` |
| GET | `/api/v1/programs/{program_id}/content` | Secure enrolled content — gated | `get_current_user` 403 if not enrolled | `{phases,goals}` |
| GET | `/api/v1/programs/{program_id}/meeting-link` | Secure meeting link | `get_current_user` 403 if not enrolled | `{meeting_links:[]}` from `activities meeting_link` |
| POST/GET | `/api/v1/programs/{program_id}/check-ins` | Checkins | `admin,provider` for list, `get_current_user` for create (`?participant_email=&phase_id=`) | `CheckinCreate {participant_email,phase_id,notes}` → attendance |
| GET | `/api/v1/programs/{program_id}/progress` | Progress | `get_current_user` `?participant_email=` | `{overall,stage:[{stage_number,completion_percent}],milestone:[{achieved,achieved_at}],attendance:{total_phases,attended,rate},activities,assessments:{surveys},missed_tasks:[],certificate_url}` `d1a680f` |
| GET | `/api/v1/programs/{program_id}/dashboards/participant` | Participant dashboard | `get_current_user` | `{enrolment_status,phases,recent_activities,overall_progress,stage,milestone,attendance,missed_tasks,certificate_url,goals}` |
| GET | `/api/v1/programs/{program_id}/dashboards/provider` | Provider dashboard | `admin,provider` | `{total_enrolments,by_status,by_phase,capacity_utilization}` |
| PUT | `/api/v1/programs/{program_id}/goals` | Goal & outcome — configurable fields | `admin,provider` | `{goals:{}}` → `Program.goals` |
| GET | `/api/v1/programs/{program_id}/certificate` | Completion certificate | `get_current_user` `?participant_email=` | `400` if `overall<100`, `→ {certificate_url,provider_acknowledgement}` |
| PATCH | `/api/v1/programs/{program_id}/enrolments/{enrol_id}/status` | Completion/withdrawal/cancellation/extension | `admin,provider` | `{status:completed|withdrawn|cancelled|extended|enrolled|active}` |
| POST | `/api/v1/programs/{program_id}/surveys` | Surveys | `admin,provider` | `SurveyCreate {title,description,questions:[{text,type}]}` |
| POST | `/api/v1/programs/{program_id}/reviews` | Reviews (verified) | `get_current_user` (400 if not enrolled) | `ReviewCreate {rating 1-5,comment,participant_email}` → `verified:true` `1bf6be0` |
| GET | `/api/v1/programs/{program_id}/reports` | Reports | `admin,provider` | `?type=enrolment|attendance|engagement|assessment|progress|completion|revenue` `1bf6be0` |
| GET | `/api/v1/programs/{program_id}/enrolments/export` | Export CSV | `admin,provider` | `text/csv` |
| GET | `/api/v1/programs/reports/summary` | Summary | `admin,provider` | `?enterprise_id=` `{total_programs,by_status,by_category,by_delivery_mode,total_enrolments,total_checkins}` |

---

## Admin — Approvals — 16 ops `admin.py:14` `get_current_admin` (role==admin)

| Method | Path | Summary |
|---|---|---|
| GET | `/api/v1/admin/events/pending` | Pending Events Queue `?enterprise_id=&category=&page=&page_size=` → `get_events_service status=pending_approval` |
| POST | `/api/v1/admin/events/{event_id}/approve` | Approve → `approved` |
| POST | `/api/v1/admin/events/{event_id}/reject` | Reject → `cancelled` |
| POST | `/api/v1/admin/events/{event_id}/publish` | Publish Approved → `published` |
| GET/POST | `/api/v1/admin/trainings/pending` + `/{id}/approve|reject|publish` | Same for trainings `pending_approval` |
| GET/POST | `/api/v1/admin/programs/pending` + `/{id}/approve|reject|publish` | Same for programs |
| GET/POST/DELETE | `/api/v1/admin/event-categories` `/{category_id}` | List/Create/Delete `EventCategory {name,parent_id→subcategory,description}` |
| GET | `/api/v1/admin/event-audits/{event_id}` | Audit history `[EventAudit {action:status_change,before,after,created_at}]` `event_aux_models.py:7` |

---

## Auth — 7 ops

| Method | Path | Summary | Frontend Use |
|---|---|---|---|
| POST | `/api/v1/auth/chat-token` | Issue Web Session Chat Token | — |
| GET | `/api/v1/auth/integration` | Auth Integration Reference | Show issuer/audience/JWKS to devs |
| GET | `/api/v1/auth/test-users` | List Static Test User IDs | Dev picker |
| GET/POST | `/api/v1/auth/dev-token` | Generate Development JWT | `ENABLE_DEV_TOKEN=true` only |
| GET | `/api/v1/auth/tenants` | List Tenants | Tenant selector |
| GET | `/api/v1/auth/session` | Get Web Session (Frontend Auth Check) | `fetch /auth/session` with `credentials:include` → `{authenticated, data:{user}}` |

---

## Frontend Usage Patterns (copy-paste)

**Auth header:** `Authorization: Bearer <access_token>` (Keycloak) or cookie auto. `fetch("https://chat.wisdomtooth.tech/api/v1/auth/session",{credentials:"include"}).then(r=>r.json()).then(j=>j.authenticated)`

**Create under approved business:** POST enterprise `status:active` first, else `400 Enterprise not approved`.

**Publish flow (after e711778):** `POST /events {status:draft}` → `PATCH /events/{id}/status {"status":"pending_approval"}` → **admin** `POST /admin/events/{id}/approve` → `PATCH {"status":"published"}` **provider now allowed** (or `POST /events/{id}/unpublish`→`approved` then `PATCH published` without second approval). Same for trainings/programs (`approved` only needs admin).

**Cancel/Restore (Events):** `PATCH {"status":"cancelled"}` any status → `PATCH {"status":"draft"}` → `PATCH {"status":"published"}` (provider) + new regs blocked `400 event is cancelled` `ec618d6`. `reason` optional.

**Archive:** `POST /events/{id}/archive` → `archived` for `draft/approved/suspended/cancelled/completed→archived`, not `pending_approval/published` (must `approve→draft` or `complete/suspend` first). No second approval needed now.

**Paid flow:** `POST /events/{id}/checkout {ticket_type_id,quantity,payment_provider}` → `201 EventOrder amount/currency` (stub `confirmed`). `GET /events/{id}/orders` to track. `POST .../refund` → `refund_requested` (`403` if `attended`).

**Capacity:** `GET /events/{id}` `available_seats/is_full`; `POST /registrations` `400 Please join waitlist`; `POST /waitlist` `201` `DELETE /waitlist/{entry_id}`.

**Check-in:** `POST /events/{id}/check-in {qr_code|registration_id,session_id}` → `attended` `checked_in_at/by` `s1t2u3v4w5x6`, `POST /validate-qr` for scanner, `GET /registrations/{id}/qr` PNG.

**Calendar:** `GET /events/{id}/calendar.ics` and `GET /trainings/{id}/calendar.ics` `text/calendar` download `Content-Disposition`. Use `<a href=".../calendar.ics">Add to calendar</a>`.

**Meeting links:** `GET /events/{id}/meeting-link` + `GET /trainings/{id}/meeting-link` + `GET /programs/{id}/meeting-link` → `403 Enrolled participants only` if not `confirmed|attended/enrolled` (hide button).

**Gated content:** `GET /trainings/{id}/content` and `GET /programs/{id}/content` `403` if not enrolled — gate behind `GET /my/enrolments`.

**Progress/Certificate:** `POST /trainings/{id}/progress/complete-lesson {lesson_id}` → `{overall_percent,certificate_url,resume_lesson,mandatory_done}`; `GET /trainings/{id}/certificate?participant_email=&progress` → `403 access expired` if `access_expires_at` passed (`access_duration_days`). Same for programs `GET /programs/{id}/progress` + `certificate`.

**Assessments:** `POST /trainings/{id}/assessments {title,level,pass_mark,attempt_limit,time_limit_minutes,publication,publish_at,randomize}` → `POST .../assessments/{aid}/questions {question_text,question_type,options,correct_answer,points,explanation,reusable}` → `GET .../assessments?randomize=true` shuffles; `POST .../submit {answers}` auto-scored `multiple_select` comma-set, `short_answer/essay` `needs_manual:true` → `POST .../submissions/{sid}/grade {score,feedback}` manual; `GET .../submissions/{sid}/review` explanations. `GET /question-bank` reusable.

**Programs engagement:** `GET /programs/{id}/availability` `{available_seats,is_full,waitlist_count,is_free}`; `GET /programs/my/enrolments?status=` for dashboard; `PUT /programs/{id}/goals` configurable; `GET /dashboards/participant|provider`; `GET /programs/{id}/reports?type=enrolment|attendance|engagement|assessment|progress|completion|revenue`; `GET .../enrolments/export` CSV.

**Postman:** Import `postman/Classifieds-Marketplace-Platform.postman_collection.json` set `{{baseUrl}}=https://chat.wisdomtooth.tech/api/v1`, `{{enterpriseId}}`, `{{locationId}}`. For new routes use `https://chat.wisdomtooth.tech/api/docs` Try-it (Bearer).

**Curl (Events):**
```bash
# Create (provider)
curl -X POST https://chat.wisdomtooth.tech/api/v1/events/ -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"enterprise_id":"$EID","title":"Demo","category":"Fitness","start_date":"2026-09-01T09:00:00Z","end_date":"2026-09-01T12:00:00Z","duration_type":"half_day","delivery_mode":"hybrid","venue":{"address":"123 Main"},"meeting_provider":"zoom"}'
# Publish (provider after first approved)
curl -X PATCH https://chat.wisdomtooth.tech/api/v1/events/$ID/status -H "Authorization: Bearer $TOKEN" -d '{"status":"published"}'
# Archive (provider, draft)
curl -X POST https://chat.wisdomtooth.tech/api/v1/events/$ID/archive -H "Authorization: Bearer $TOKEN"
```
