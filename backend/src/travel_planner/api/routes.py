from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from travel_planner.domain.errors import AppError
from travel_planner.domain.models import (
    KnowledgeCreate,
    KnowledgeDetail,
    KnowledgeHit,
    KnowledgeSource,
    Model,
    PreferenceProfile,
    PreferencePut,
    ShortText,
    Trip,
    TripFields,
    TripPatch,
)
from travel_planner.knowledge.public_web import MAX_BYTES, validate_url
from travel_planner.planner.models import Itinerary, ItineraryEdit, JobResume, JobSubmit, PlanJob
from travel_planner.providers.models import Place
from travel_planner.storage.database import SCHEMA_VERSION
from travel_planner.storage.runtime import runtime_status

router = APIRouter(prefix="/api")
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


@router.get("/health")
def health(request: Request):
    with request.app.state.db.connect() as connection:
        connection.execute("SELECT 1").fetchone()
    return {"status": "ok", "schema_version": SCHEMA_VERSION}


@router.get("/runtime")
def get_runtime(request: Request):
    with request.app.state.db.connect() as connection:
        return runtime_status(connection)


@router.get("/config/status")
def config_status(request: Request):
    return request.app.state.settings.status()


class ServiceCheckRequest(Model):
    service: Literal["llm", "search", "amap"]
    destination: ShortText = "西安"


@router.post("/config/check")
async def check_service(body: ServiceCheckRequest, request: Request):
    result = await request.app.state.services.check(body.service, body.destination)
    return await run_in_threadpool(request.app.state.repository.save_service_check, result)


@router.get("/config/checks")
def list_service_checks(request: Request):
    return request.app.state.repository.list_service_checks()


@router.post("/trips", response_model=Trip, status_code=201)
def create_trip(body: TripFields, request: Request):
    return request.app.state.repository.create_trip(body)


@router.get("/trips", response_model=list[Trip])
def list_trips(request: Request, limit: Limit = 50, offset: Offset = 0):
    return request.app.state.repository.list_trips(limit, offset)


@router.get("/trips/{trip_id}", response_model=Trip)
def get_trip(trip_id: str, request: Request):
    return request.app.state.repository.get_trip(trip_id)


@router.patch("/trips/{trip_id}", response_model=Trip)
def patch_trip(trip_id: str, body: TripPatch, request: Request):
    return request.app.state.repository.patch_trip(trip_id, body)


@router.post("/trips/{trip_id}/jobs", response_model=PlanJob, status_code=202)
async def submit_job(trip_id: str, body: JobSubmit, request: Request):
    job = await run_in_threadpool(
        request.app.state.jobs.submit,
        trip_id,
        body,
        configured=request.app.state.settings.status()["ready_for_planning"],
    )
    request.app.state.planner.wake.set()
    return job


@router.get("/trips/{trip_id}/jobs", response_model=list[PlanJob])
def list_jobs(trip_id: str, request: Request):
    return request.app.state.jobs.list_jobs(trip_id)


@router.get("/jobs/{job_id}", response_model=PlanJob)
def get_job(job_id: str, request: Request):
    return request.app.state.jobs.get(job_id)


@router.post("/jobs/{job_id}/retry", response_model=PlanJob, status_code=202)
async def retry_job(job_id: str, body: JobSubmit, request: Request):
    original = await run_in_threadpool(request.app.state.jobs.get, job_id)
    job = await run_in_threadpool(
        request.app.state.jobs.submit,
        original["trip_id"],
        body,
        retry_of=job_id,
        configured=request.app.state.settings.status()["ready_for_planning"],
    )
    request.app.state.planner.wake.set()
    return job


@router.post("/jobs/{job_id}/resume", response_model=PlanJob, status_code=202)
async def resume_job(job_id: str, body: JobResume, request: Request):
    job = await run_in_threadpool(
        request.app.state.jobs.resume,
        job_id,
        body.expected_revision,
        configured=request.app.state.settings.status()["ready_for_planning"],
    )
    request.app.state.planner.wake.set()
    return job


@router.get("/trips/{trip_id}/itinerary", response_model=Itinerary)
def get_itinerary(
    trip_id: str, request: Request, revision: Annotated[int | None, Query(ge=1)] = None
):
    return request.app.state.jobs.itinerary(trip_id, revision)


@router.patch("/trips/{trip_id}/itinerary", response_model=PlanJob, status_code=202)
async def edit_itinerary(trip_id: str, body: ItineraryEdit, request: Request):
    job = await run_in_threadpool(
        request.app.state.jobs.submit_edit,
        trip_id,
        body,
        configured=request.app.state.settings.status()["ready_for_planning"],
    )
    request.app.state.planner.wake.set()
    return job


@router.get("/trips/{trip_id}/places", response_model=list[Place])
async def lookup_places(
    trip_id: str, request: Request, q: Annotated[str, Query(min_length=1, max_length=80)]
):
    trip = await run_in_threadpool(request.app.state.repository.get_trip, trip_id)
    places = await request.app.state.services.amap.places(trip["destination"], q, 25)
    return [
        place
        for place in places
        if place.city and place.city.removesuffix("市") == trip["destination"].removesuffix("市")
    ]


@router.get("/preferences", response_model=PreferenceProfile)
def get_preferences(request: Request):
    return request.app.state.repository.get_preferences()


@router.put("/preferences", response_model=PreferenceProfile)
def put_preferences(body: PreferencePut, request: Request):
    return request.app.state.repository.put_preferences(body)


@router.post("/knowledge", response_model=KnowledgeSource, status_code=202)
def create_knowledge(body: KnowledgeCreate, request: Request):
    if body.url:
        validate_url(body.url)
    raw = (body.text if body.kind == "text" else body.url).encode("utf-8")
    return request.app.state.knowledge.create(body.kind, body.title, raw, body.url)


@router.post("/knowledge/files", response_model=KnowledgeSource, status_code=202)
async def upload_knowledge(
    request: Request,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form(min_length=1, max_length=500)],
):
    try:
        raw = await file.read(MAX_BYTES + 1)
    finally:
        await file.close()
    if len(raw) > MAX_BYTES:
        raise AppError("source_too_large", "单份资料最多 20 MB", 413)
    if not title.strip():
        raise AppError("invalid_title", "请填写资料标题", 422)
    name = (file.filename or "").lower()
    if raw.startswith(b"%PDF-"):
        kind = "pdf"
    elif name.endswith((".txt", ".md")):
        kind = "text"
    elif name.endswith(".pdf"):
        raise AppError("invalid_pdf", "文件缺少 PDF 标识，请上传有效 PDF", 422)
    else:
        raise AppError("unsupported_file", "支持 PDF 或 UTF-8 的 TXT、Markdown 文件", 422)
    return await run_in_threadpool(request.app.state.knowledge.create, kind, title.strip(), raw)


@router.get("/knowledge", response_model=list[KnowledgeSource])
def list_knowledge(request: Request, limit: Limit = 50, offset: Offset = 0):
    return request.app.state.knowledge.list_sources(limit, offset)


@router.get("/knowledge/search", response_model=list[KnowledgeHit])
def search_knowledge(
    request: Request,
    q: Annotated[str, Query(min_length=1, max_length=1000)],
    limit: Annotated[int, Query(ge=1, le=8)] = 8,
):
    return request.app.state.knowledge.search(q, limit)


@router.get("/knowledge/{source_id}", response_model=KnowledgeDetail)
def get_knowledge(source_id: str, request: Request):
    return request.app.state.knowledge.get(source_id)


@router.get("/knowledge/{source_id}/original", response_class=FileResponse)
def download_original(source_id: str, request: Request):
    path, filename = request.app.state.knowledge.original(source_id)
    return FileResponse(path, filename=filename, media_type="application/octet-stream")


@router.post("/knowledge/{source_id}/retry", response_model=KnowledgeSource)
def retry_knowledge(source_id: str, request: Request):
    return request.app.state.knowledge.retry(source_id)


@router.delete("/knowledge/{source_id}", status_code=204)
def delete_knowledge(source_id: str, request: Request):
    request.app.state.knowledge.delete(source_id)
    return Response(status_code=204)
