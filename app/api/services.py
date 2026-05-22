"""Service management API routes."""
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import SERVICES_DIR
from app.database import get_session
from app.models.service import Service
from app.models.module import Module
from app.models.service_module import ServiceModule
from app.schemas.service import (
    ServiceCreate, ServiceUpdate, ServiceResponse, ServiceStatus,
    CodeUpdate, ConfigUpdate, ServiceDepsResponse, ModuleDepSource,
    RequirementsUpdate, ModuleScanSource, ScanComparisonItem,
    ServiceScanResultItem,
)
from app.schemas.module import PkgStatusItem, DepsInstallResponse, ScannedImportItem, SourceScanResultItem
from app.core.service_manager import ServiceManager, ServiceNotFoundError, ServiceManagerError
from app.utils.dependency import check_requirements, install_requirements
from app.utils.import_scanner import scan_source_file, build_scan_comparison
from app.utils.validation import validate_requirements

router = APIRouter(prefix="/services", tags=["services"])
service_manager = ServiceManager()


@router.get("", response_model=list[ServiceResponse])
async def list_services(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Service).order_by(Service.created_at.desc()))
    return list(result.scalars().all())


@router.post("", response_model=ServiceResponse, status_code=201)
async def create_service(data: ServiceCreate, session: AsyncSession = Depends(get_session)):
    try:
        service = await service_manager.create(
            session=session,
            name=data.name,
            display_name=data.display_name,
            description=data.description,
            code_source=data.code_source,
            code=data.code,
            python_path=data.python_path,
            auto_restart=data.auto_restart,
        )
        return service
    except ServiceManagerError as e:
        detail_msg = f"{str(e)}: {e.detail}" if hasattr(e, 'detail') else str(e)
        raise HTTPException(status_code=400, detail=detail_msg)


@router.get("/{name}", response_model=ServiceResponse)
async def get_service(name: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Service).where(Service.name == name))
    service = result.scalar_one_or_none()
    if service is None:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    return service


@router.delete("/{name}", status_code=204)
async def delete_service(name: str, session: AsyncSession = Depends(get_session)):
    try:
        await service_manager.delete(session, name)
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")


@router.post("/{name}/start", response_model=ServiceResponse)
async def start_service(name: str, session: AsyncSession = Depends(get_session)):
    try:
        return await service_manager.start(session, name)
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    except ServiceManagerError as e:
        detail_msg = f"{str(e)}: {e.detail}" if hasattr(e, 'detail') else str(e)
        raise HTTPException(status_code=500, detail=detail_msg)


@router.post("/{name}/stop", response_model=ServiceResponse)
async def stop_service(name: str, session: AsyncSession = Depends(get_session)):
    try:
        return await service_manager.stop(session, name)
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    except ServiceManagerError as e:
        detail_msg = f"{str(e)}: {e.detail}" if hasattr(e, 'detail') else str(e)
        raise HTTPException(status_code=500, detail=detail_msg)


@router.post("/{name}/restart", response_model=ServiceResponse)
async def restart_service(name: str, session: AsyncSession = Depends(get_session)):
    try:
        return await service_manager.restart(session, name)
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    except ServiceManagerError as e:
        detail_msg = f"{str(e)}: {e.detail}" if hasattr(e, 'detail') else str(e)
        raise HTTPException(status_code=500, detail=detail_msg)


@router.post("/{name}/enable", response_model=ServiceResponse)
async def enable_service(name: str, session: AsyncSession = Depends(get_session)):
    try:
        return await service_manager.enable(session, name)
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    except ServiceManagerError as e:
        detail_msg = f"{str(e)}: {e.detail}" if hasattr(e, 'detail') else str(e)
        raise HTTPException(status_code=500, detail=detail_msg)


@router.post("/{name}/disable", response_model=ServiceResponse)
async def disable_service(name: str, session: AsyncSession = Depends(get_session)):
    try:
        return await service_manager.disable(session, name)
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    except ServiceManagerError as e:
        detail_msg = f"{str(e)}: {e.detail}" if hasattr(e, 'detail') else str(e)
        raise HTTPException(status_code=500, detail=detail_msg)


@router.get("/{name}/status", response_model=ServiceStatus)
async def get_service_status(name: str, session: AsyncSession = Depends(get_session)):
    try:
        status = await service_manager.get_status(session, name)
        return ServiceStatus(
            name=name,
            active=status.get("active", "unknown"),
            enabled=status.get("enabled", False),
            pid=status.get("pid"),
            uptime=None,
        )
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")


# ── Code Management ────────────────────────────────────────────

@router.get("/{name}/code")
async def get_service_code(name: str, session: AsyncSession = Depends(get_session)):
    try:
        code = await service_manager.get_code(session, name)
        return {"code": code}
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")


@router.put("/{name}/code")
async def update_service_code(name: str, data: CodeUpdate, session: AsyncSession = Depends(get_session)):
    try:
        service = await service_manager.update_code(session, name, data.code)
        return {
            "message": "Code updated",
            "status": service.status,
            "needs_restart": service.status == "running",
        }
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    except ServiceManagerError as e:
        detail_msg = f"{str(e)}: {e.detail}" if hasattr(e, 'detail') else str(e)
        raise HTTPException(status_code=400, detail=detail_msg)


@router.post("/{name}/upload")
async def upload_service_script(name: str, file: UploadFile = File(...), session: AsyncSession = Depends(get_session)):
    if not file.filename.endswith(".py"):
        raise HTTPException(status_code=400, detail="Only .py files are allowed")

    content = (await file.read()).decode("utf-8")
    try:
        service = await service_manager.update_code(session, name, content)
        return {"message": "Script uploaded", "filename": file.filename}
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    except ServiceManagerError as e:
        detail_msg = f"{str(e)}: {e.detail}" if hasattr(e, 'detail') else str(e)
        raise HTTPException(status_code=400, detail=detail_msg)


# ── Dependency Management ──────────────────────────────────────

async def _get_service_deps_data(name: str, session: AsyncSession, scan: bool = False):
    """Collect all dependency data for a service (self + enabled modules).

    When scan=True, also analyzes source code imports via AST scanning.
    """
    result = await session.execute(select(Service).where(Service.name == name))
    service = result.scalar_one_or_none()
    if service is None:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")

    # Check service own requirements
    svc_reqs = service.requirements_list
    svc_check = check_requirements(svc_reqs)
    svc_items = [PkgStatusItem(**vars(s)) for s in svc_check.requirements]

    # Collect enabled module requirements
    bindings_result = await session.execute(
        select(ServiceModule).where(
            ServiceModule.service_id == service.id,
            ServiceModule.enabled == True,  # noqa: E712
        )
    )
    bindings = bindings_result.scalars().all()

    module_deps: list[ModuleDepSource] = []
    all_specs: list[str] = list(svc_reqs)

    for binding in bindings:
        mod_result = await session.execute(select(Module).where(Module.id == binding.module_id))
        mod = mod_result.scalar_one_or_none()
        if mod and mod.requirements_list:
            mod_reqs = mod.requirements_list
            mod_check = check_requirements(mod_reqs)
            mod_items = [PkgStatusItem(**vars(s)) for s in mod_check.requirements]
            module_deps.append(ModuleDepSource(
                module_name=mod.name,
                module_display_name=mod.display_name,
                requirements=mod_items,
            ))
            all_specs.extend(mod_reqs)

    # Aggregate check (all requirements combined)
    agg_check = check_requirements(all_specs)
    agg_items = [PkgStatusItem(**vars(s)) for s in agg_check.requirements]

    # Build base response
    response = ServiceDepsResponse(
        service_requirements=svc_items,
        module_requirements=module_deps,
        all_requirements=agg_items,
        all_satisfied=agg_check.all_satisfied,
        missing_count=len(agg_check.missing) + len(agg_check.unsatisfied),
    )

    # Source code scanning (optional)
    if scan:
        service_dir = SERVICES_DIR / service.name
        # Scan service main.py
        svc_scan_result = scan_source_file(service_dir / "main.py", service_dir)
        svc_scan_item = SourceScanResultItem(
            file_path=svc_scan_result.file_path,
            imports=[ScannedImportItem(**vars(imp)) for imp in svc_scan_result.imports],
            third_party_packages=svc_scan_result.third_party_packages,
            error=svc_scan_result.error,
        )

        # Scan enabled module files
        module_scans: list[ModuleScanSource] = []
        all_scanned_packages: list[str] = list(svc_scan_result.third_party_packages)

        for binding in bindings:
            mod_result = await session.execute(select(Module).where(Module.id == binding.module_id))
            mod = mod_result.scalar_one_or_none()
            if mod and mod.script_path:
                mod_path = Path(mod.script_path)
                mod_scan_result = scan_source_file(mod_path)
                mod_scan_item = SourceScanResultItem(
                    file_path=mod_scan_result.file_path,
                    imports=[ScannedImportItem(**vars(imp)) for imp in mod_scan_result.imports],
                    third_party_packages=mod_scan_result.third_party_packages,
                    error=mod_scan_result.error,
                )
                module_scans.append(ModuleScanSource(
                    module_name=mod.name,
                    module_display_name=mod.display_name,
                    scan=mod_scan_item,
                ))
                all_scanned_packages.extend(mod_scan_result.third_party_packages)

        # Build scan comparison
        comparison = build_scan_comparison(all_specs, all_scanned_packages)

        response.scanned_imports = ServiceScanResultItem(
            service_scan=svc_scan_item,
            module_scans=module_scans,
            all_third_party=all_scanned_packages,
        )
        response.scan_comparison = ScanComparisonItem(
            matched=comparison.matched,
            scanned_only=comparison.scanned_only,
            declared_only=comparison.declared_only,
        )

    return response


@router.get("/{name}/deps", response_model=ServiceDepsResponse)
async def check_service_deps(name: str, scan: bool = False, session: AsyncSession = Depends(get_session)):
    """Get aggregated dependency status for a service (self + enabled modules).

    When scan=true, also analyzes source code imports via AST scanning.
    """
    return await _get_service_deps_data(name, session, scan=scan)
    return await _get_service_deps_data(name, session)


@router.post("/{name}/deps/install", response_model=DepsInstallResponse)
async def install_service_deps(name: str, session: AsyncSession = Depends(get_session)):
    """Install all missing dependencies for a service (self + enabled modules)."""
    result = await session.execute(select(Service).where(Service.name == name))
    service = result.scalar_one_or_none()
    if service is None:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")

    # Collect all requirements
    all_specs = list(service.requirements_list)

    bindings_result = await session.execute(
        select(ServiceModule).where(
            ServiceModule.service_id == service.id,
            ServiceModule.enabled == True,  # noqa: E712
        )
    )
    bindings = bindings_result.scalars().all()

    for binding in bindings:
        mod_result = await session.execute(select(Module).where(Module.id == binding.module_id))
        mod = mod_result.scalar_one_or_none()
        if mod and mod.requirements_list:
            all_specs.extend(mod.requirements_list)

    install_result = await install_requirements(all_specs)
    return DepsInstallResponse(
        success=install_result.success,
        installed=install_result.installed,
        failed=install_result.failed,
        output=install_result.output,
    )


@router.put("/{name}/requirements", response_model=ServiceResponse)
async def update_service_requirements(name: str, data: RequirementsUpdate, session: AsyncSession = Depends(get_session)):
    """Update service's own dependency declarations."""
    result = await session.execute(select(Service).where(Service.name == name))
    service = result.scalar_one_or_none()
    if service is None:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")

    # Validate requirement specs
    errors = validate_requirements(data.requirements)
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))

    service.requirements_list = data.requirements
    service.updated_at = datetime.now()
    try:
        await session.commit()
        await session.refresh(service)
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    return service
