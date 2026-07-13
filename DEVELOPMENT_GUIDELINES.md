# DEVELOPMENT_GUIDELINES.md

Guidelines for building large-scale web platforms from inception to operations.
These rules apply across the full lifecycle: planning, design, development, testing, deployment, and maintenance.

**Goal:** Every phase produces verifiable output. Nothing proceeds on assumptions.

---

## 1. Project Initiation

**Define why before defining what.**

Before any design or code:
- State the project background, objectives, and target users.
- Define the problem being solved and the expected outcome.
- Set the project scope explicitly — and list what is out of scope.
- Identify timeline, team size, budget constraints, and success criteria.

Required outputs:
- Project charter
- Scope statement
- Stakeholder list
- Preliminary timeline
- Initial feature list

The test: Can a new team member read these documents and explain the project in five minutes?

## 2. Requirements Analysis

**Inventory everything before designing anything.**

Collect requirements across three dimensions:

User requirements:
- User roles and use cases
- Workflows, query needs, edit needs
- Import / export / notification needs

Data requirements:
- Data sources, formats, and field definitions
- Update frequency, data owners, cleaning rules
- Historical data retention needs

Permission requirements:
- Which roles can view, create, edit, and delete which data
- Admin configuration scope

Required outputs:
- Requirements specification
- User stories
- Feature list
- Permission matrix
- Data source inventory
- Workflow diagrams

Never start system design until requirements are signed off. Undiscovered requirements discovered in development cost 10× more to fix.

## 3. System Architecture

**Design the skeleton before adding muscle.**

Define each layer explicitly:

Frontend:
- Page structure, UI components, API integration pattern
- State management, error display, loading states, responsive design

Backend:
- API structure, business logic, auth/permission layer
- File handling, scheduled jobs, logging

Database:
- Table design, field definitions, relationships, indexes
- Audit trail strategy, backup strategy

Integrations — identify which of the following apply:
- File formats: Excel, CSV, JSON
- Databases: PostgreSQL, SQL Server
- Auth: AD / SSO
- External systems: Email, ERP, AI API, GitHub

Required outputs:
- System architecture diagram
- Data flow diagram
- API specification
- Database design document
- Deployment architecture diagram
- Security design document

## 4. UI / UX Design

**Users must understand the interface without training.**

Design priorities (in order):
- Show important information first, not what's easiest to build.
- Query and filter interactions must be fast and obvious.
- Forms must guide users — validate inline, not on submit.
- Errors must explain what went wrong and how to fix it.
- Actions unavailable due to permissions must be hidden or disabled, not just error on click.
- All views must work on both mobile and large screens.

Pages every platform needs:
- Login
- Home dashboard
- Data query / list
- Detail view
- Analytics / charts
- Admin settings
- User permissions
- Import / export
- System logs

Required outputs:
- Wireframes
- Visual mockups
- Interactive prototype
- UI component specification
- Page flow diagram

## 5. Database Design

**Model data for correctness first, performance second, flexibility third.**

Rules:
- Consistent naming convention across all tables and columns.
- No redundant data — normalise unless there's a measured performance reason not to.
- All required fields have validation rules.
- All important records have audit trails (who changed what, when).
- Frequently queried columns have indexes.
- Every table has `created_at` and `updated_at`.

Common tables most platforms need:

| Table | Purpose |
|---|---|
| `users` | User accounts |
| `roles` | Role definitions |
| `permissions` | Role–permission mapping |
| `projects` | Top-level domain entities |
| `tasks` | Work items |
| `attachments` | File references |
| `audit_logs` | User action history |
| `system_logs` | System-level events |

Required outputs:
- ERD
- Table specification
- Field definition table
- Data dictionary
- Migration scripts

## 6. Backend Development

**The backend owns data integrity, security, and business logic. Nothing else should.**

Build in this order:
1. Project structure and configuration
2. Auth and session management
3. Role-based permission layer (centralised — not scattered across routes)
4. Core CRUD endpoints
5. Query endpoints with filtering and pagination
6. File upload handling
7. Import (Excel / CSV) with validation and error reporting
8. Export
9. Scheduled jobs
10. Logging and error handling

API design rules:
- Clear, consistent naming. REST conventions unless there's a good reason not to.
- Request and response formats are consistent across all endpoints.
- Errors return standardised `{ "error": "..." }` with correct HTTP status codes.
- Sensitive data never leaves the backend in raw form.
- Every list endpoint supports pagination.
- Every state-changing operation is logged.

Common endpoints:

| Endpoint | Purpose |
|---|---|
| `POST /api/login` | Authentication |
| `GET/POST /api/users` | User management |
| `GET/POST /api/projects` | Domain entities |
| `GET /api/dashboard` | Aggregated stats |
| `POST /api/upload` | File upload |
| `GET /api/export` | Data export |
| `GET /api/settings` | System configuration |
| `GET /api/logs` | System log access |

## 7. Frontend Development

**The frontend's job is clarity, not cleverness.**

Build in this order:
1. Page routing
2. API integration layer
3. Dashboard
4. Data tables (with sort and filter)
5. Query / filter UI
6. Create / edit forms
7. Charts and analytics
8. Modals and dialogs
9. Import / export UI
10. Error states and loading states
11. Permission-aware display logic
12. Responsive layout

Rules:
- Important information is above the fold.
- Buttons and controls are in consistent positions across pages.
- Tables support sorting and filtering. Large datasets paginate or use virtual scroll.
- Error messages are written for users, not developers.
- Charts always have units, labels, and a title.
- Export filenames include the data scope and timestamp.

## 8. Data Integration and Scheduling

**External data pipelines fail silently unless you build in visibility.**

Before building any integration:
- Confirm the exact source format and field mapping.
- Define what happens to invalid or duplicate records.
- Define whether imports overwrite or append.

Import rules:
- Validate format before processing. Reject and report bad records — never silently skip.
- Every import creates a batch record: timestamp, row count, error count, operator.
- Never overwrite existing records without an explicit confirmation step.

Scheduled job rules:
- Every job has a start time, end time, and status in the log.
- Failures produce an actionable error message — never a silent no-op.
- Jobs must be re-runnable manually without side effects.
- Duplicate execution must be prevented (lock or idempotency key).

## 9. Testing and Acceptance

**Test at every boundary. Never ship untested permission logic.**

Testing types and what they cover:

| Type | Covers |
|---|---|
| Unit | Individual functions and business logic |
| API | Input/output contracts for every endpoint |
| Integration | Frontend ↔ backend ↔ database full paths |
| UI | User interactions and visual states |
| Permission | Every role × every operation combination |
| Data | Calculated fields, aggregations, report accuracy |
| Load | Performance under realistic and peak data volumes |
| UAT | End-to-end acceptance by actual users |

Rules:
- Every feature has test cases before it ships.
- Every role is tested against its permission boundaries.
- Every report is verified against known data.
- Import and export are tested with edge-case files.
- Error scenarios are tested, not just happy paths.
- Run a smoke test before every production deployment.
- Every bug fix has a regression test.

Required outputs:
- Test cases
- Test records
- Bug list
- UAT sign-off
- Fix log

## 10. Deployment and Go-live

**Production is different from dev. Verify everything, assume nothing.**

Deployment checklist:
```
□ Production environment provisioned
□ Database provisioned and migrated
□ Environment variables set (not hardcoded)
□ SSL certificate installed(optional)
□ Auth / SSO configured
□ Firewall rules applied
□ Scheduled jobs configured
□ Backup configured and tested
□ Log path configured and writable
□ Frontend deployed
□ Backend deployed
□ Smoke test passed
```

Go-live sign-off checklist:
```
□ Site loads
□ Login works
□ All APIs respond correctly
□ Database connection is stable
□ Permissions behave correctly for each role
□ Scheduled jobs run on schedule
□ Import and export work end-to-end
□ Logs are being written
□ Backup is running
□ Error pages render correctly
□ At least one real user has completed UAT
```

## 11. Documentation and Training

**A system no one can maintain or operate is not done.**

User documentation:
- User manual (how to use every feature)
- FAQ
- Import / export guide
- Permission guide

Operations documentation:
- Deployment guide
- Architecture overview
- Database guide
- API reference
- Scheduled job reference
- Log reference
- Incident response runbook
- Backup and restore SOP

Training must cover:
- Login and navigation
- Querying and filtering data
- Reading charts and dashboards
- Creating and editing records
- Importing data
- Exporting reports
- Handling common errors

## 12. Operations and Monitoring

**Problems you can't see are problems you can't fix.**

Monitor these at all times:

| Item | What to check |
|---|---|
| Web status | Site is reachable and returns 200 |
| API status | Endpoints respond within SLA |
| DB status | Connection pool is healthy |
| Job status | Scheduled jobs complete without error |
| Log status | No spike in ERROR-level log entries |
| Disk usage | Sufficient space for data and logs |
| Backup status | Last backup completed successfully |
| Usage | Active user counts and traffic patterns |
| Performance | Response time and slow query count |

Operations rules:
- Every system error has a log entry with enough context to diagnose.
- Scheduled job failures are traceable and alertable.
- Critical data is backed up on a defined schedule and restore is tested.
- Permission changes are logged with actor, timestamp, and what changed.
- Every production change has a version record.
- Bug fixes are recorded with root cause and resolution.
- Database capacity is reviewed regularly.
- Security posture is reviewed regularly.

## 13. Version Control and Continuous Improvement

**Ship controlled versions. Never patch production directly.**

Version control rules:
- All code is in Git. Production and development branches are separate.
- Every release has a version number.
- Every commit has a message that explains the change.
- Major releases have a Release Note.
- A rollback path exists before every deployment.
- Never edit code directly in production.

Version stages:

| Version | Meaning |
|---|---|
| v0.1 | Prototype / proof of concept |
| v0.5 | Core features in development |
| v1.0 | First production release |
| v1.x | Bug fixes and minor features |
| v2.0 | Major architecture change or redesign |

Improvement backlog — regularly review:
- User feedback
- Performance bottlenecks
- UI usability issues
- Slow queries
- Data model gaps
- Permission workflow friction
- Report feature gaps

## 14. Core Principles

**A platform is not done when features ship. It's done when it can be operated, maintained, and extended.**

Every platform built under these guidelines must be:

1. **Traceable** — requirements link to features link to tests
2. **Verifiable** — data and calculations can be independently confirmed
3. **Controlled** — permissions are explicit and auditable
4. **Deployable** — environment setup is documented and repeatable
5. **Observable** — errors surface in logs, not just in user complaints
6. **Handoff-ready** — documentation is complete enough for a new team to operate it
7. **Extensible** — architecture accommodates new features without rewrites
8. **Recoverable** — every release can be rolled back
9. **Sustainable** — operations can continue without the original developers
10. **Understandable** — users can accomplish their goals without calling support

---

**These guidelines are working if:** requirements are stable before development starts, each phase produces documents the next phase can rely on, no feature ships without a test, no deployment happens without a checklist, and six months after go-live the system can be operated by someone who wasn't on the original team.
