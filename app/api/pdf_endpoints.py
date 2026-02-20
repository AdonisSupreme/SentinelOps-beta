"""
PDF Generation API Endpoints for SentinelOps
Handles checklist instance PDF extraction and download
"""

import os
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Depends, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, UUID4
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

from app.services.pdf_service import generate_checklist_pdf
from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/pdf", tags=["PDF Generation"])

# Database connection
def get_db_connection():
    """Get database connection"""
    try:
        conn = psycopg2.connect(settings.DATABASE_URL)
        return conn
    except Exception as e:
        logger.error(f"Database connection error: {str(e)}")
        raise HTTPException(status_code=500, detail="Database connection failed")

class PDFRequest(BaseModel):
    instance_id: UUID4
    include_summary: bool = True
    include_details: bool = True
    include_metadata: bool = True

class PDFResponse(BaseModel):
    success: bool
    message: str
    filename: Optional[str] = None
    size_bytes: Optional[int] = None

@router.post("/generate", response_model=PDFResponse)
async def generate_checklist_pdf_endpoint(request: PDFRequest):
    """
    Generate PDF for a specific checklist instance
    """
    try:
        # Extract checklist instance data
        instance_data = await extract_checklist_data(str(request.instance_id))
        
        if not instance_data:
            raise HTTPException(status_code=404, detail="Checklist instance not found")
        
        # Generate PDF
        pdf_bytes = generate_checklist_pdf(instance_data)
        
        # Create filename
        template_name = instance_data.get('template_name', 'checklist').replace(' ', '_')
        date_str = instance_data.get('checklist_date', datetime.now().strftime('%Y-%m-%d'))
        shift = instance_data.get('shift', 'unknown')
        filename = f"SentinelOps_{template_name}_{date_str}_{shift}.pdf"
        
        # Store PDF temporarily (optional - for caching)
        pdf_path = f"temp/{filename}"
        os.makedirs("temp", exist_ok=True)
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)
        
        return PDFResponse(
            success=True,
            message="PDF generated successfully",
            filename=filename,
            size_bytes=len(pdf_bytes)
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PDF generation error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {str(e)}")

@router.get("/download/{instance_id}")
async def download_checklist_pdf(instance_id: str):
    """
    Download PDF for a specific checklist instance
    """
    try:
        # Extract checklist instance data
        instance_data = await extract_checklist_data(instance_id)
        
        if not instance_data:
            raise HTTPException(status_code=404, detail="Checklist instance not found")
        
        # Generate PDF
        pdf_bytes = generate_checklist_pdf(instance_data)
        
        # Create filename
        template_name = instance_data.get('template_name', 'checklist').replace(' ', '_')
        date_str = instance_data.get('checklist_date', datetime.now().strftime('%Y-%m-%d'))
        shift = instance_data.get('shift', 'unknown')
        filename = f"SentinelOps_{template_name}_{date_str}_{shift}.pdf"
        
        # Return as streaming response
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Content-Length": str(len(pdf_bytes)),
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PDF download error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"PDF download failed: {str(e)}")

@router.get("/preview/{instance_id}")
async def preview_checklist_pdf(instance_id: str):
    """
    Preview PDF for a specific checklist instance (inline display)
    """
    try:
        # Extract checklist instance data
        instance_data = await extract_checklist_data(instance_id)
        
        if not instance_data:
            raise HTTPException(status_code=404, detail="Checklist instance not found")
        
        # Generate PDF
        pdf_bytes = generate_checklist_pdf(instance_data)
        
        # Create filename
        template_name = instance_data.get('template_name', 'checklist').replace(' ', '_')
        date_str = instance_data.get('checklist_date', datetime.now().strftime('%Y-%m-%d'))
        shift = instance_data.get('shift', 'unknown')
        filename = f"SentinelOps_{template_name}_{date_str}_{shift}.pdf"
        
        # Return as inline response
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"inline; filename={filename}",
                "Content-Length": str(len(pdf_bytes)),
                "Cache-Control": "max-age=3600"  # Cache for 1 hour
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PDF preview error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"PDF preview failed: {str(e)}")

@router.get("/instances/{instance_id}/data")
async def get_checklist_instance_data(instance_id: str):
    """
    Get checklist instance data in JSON format (for preview)
    """
    try:
        instance_data = await extract_checklist_data(instance_id)
        
        if not instance_data:
            raise HTTPException(status_code=404, detail="Checklist instance not found")
        
        return {
            "success": True,
            "data": instance_data
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Data extraction error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Data extraction failed: {str(e)}")

async def extract_checklist_data(instance_id: str) -> Optional[Dict[str, Any]]:
    """
    Extract complete checklist instance data using the provided SQL query
    """
    query = """
    WITH 
    -- Base checklist instance data
    checklist_instances_data AS (
        SELECT 
            ci.id as instance_id,
            ci.template_id,
            ci.checklist_date,
            ci.shift,
            ci.shift_start,
            ci.shift_end,
            ci.status as instance_status,
            ci.created_by,
            ci.closed_by,
            ci.closed_at,
            ci.created_at,
            ci.metadata,
            ci.completion_time_seconds,
            ci.exception_count,
            ci.section_id,
            -- Template information
            ct.name as template_name,
            ct.description as template_description,
            ct.version as template_version,
            -- Section information
            s.section_name,
            -- Created by user info
            creator.first_name || ' ' || creator.last_name as created_by_name,
            creator.email as created_by_email,
            -- Closed by user info
            closer.first_name || ' ' || closer.last_name as closed_by_name,
            closer.email as closed_by_email
        FROM checklist_instances ci
        LEFT JOIN checklist_templates ct ON ci.template_id = ct.id
        LEFT JOIN sections s ON ci.section_id = s.id
        LEFT JOIN users creator ON ci.created_by = creator.id
        LEFT JOIN users closer ON ci.closed_by = closer.id
    ),

    -- Checklist instance items with template data
    checklist_items_data AS (
        SELECT 
            cii.id as item_id,
            cii.instance_id,
            cii.template_item_id,
            cii.status as item_status,
            cii.completed_by,
            cii.completed_at,
            cii.skipped_reason,
            cii.failure_reason,
            cii.template_item_key,
            -- Template item information
            cti.title as template_item_title,
            cti.description as template_item_description,
            cti.item_type as template_item_type,
            cti.is_required as template_item_required,
            cti.scheduled_time as template_scheduled_time,
            cti.notify_before_minutes,
            cti.severity as template_severity,
            cti.sort_order as template_sort_order,
            -- Completed by user info
            completer.first_name || ' ' || completer.last_name as completed_by_name,
            completer.email as completed_by_email
        FROM checklist_instance_items cii
        LEFT JOIN checklist_template_items cti ON cii.template_item_id = cti.id
        LEFT JOIN users completer ON cii.completed_by = completer.id
    ),

    -- Checklist instance subitems
    checklist_subitems_data AS (
        SELECT 
            cis.id as subitem_id,
            cis.instance_item_id,
            cis.title as subitem_title,
            cis.description as subitem_description,
            cis.item_type as subitem_type,
            cis.is_required as subitem_required,
            cis.status as subitem_status,
            cis.completed_by,
            cis.completed_at,
            cis.skipped_reason as subitem_skipped_reason,
            cis.failure_reason as subitem_failure_reason,
            cis.severity as subitem_severity,
            cis.sort_order as subitem_sort_order,
            cis.created_at as subitem_created_at,
            -- Completed by user info
            completer.first_name || ' ' || completer.last_name as subitem_completed_by_name,
            completer.email as subitem_completed_by_email,
            -- Row number for ordering
            ROW_NUMBER() OVER (
                PARTITION BY cis.instance_item_id 
                ORDER BY cis.sort_order
            ) as subitem_row_num
        FROM checklist_instance_subitems cis
        LEFT JOIN users completer ON cis.completed_by = completer.id
    ),

    -- Pre-aggregated subitems by item
    subitems_aggregated AS (
        SELECT 
            instance_item_id,
            json_agg(
                json_build_object(
                    'subitem_id', subitem_id,
                    'title', subitem_title,
                    'description', subitem_description,
                    'item_type', subitem_type,
                    'is_required', subitem_required,
                    'severity', subitem_severity,
                    'sort_order', subitem_sort_order,
                    'status', subitem_status,
                    'completed_by', completed_by,
                    'completed_by_name', subitem_completed_by_name,
                    'completed_by_email', subitem_completed_by_email,
                    'completed_at', completed_at,
                    'skipped_reason', subitem_skipped_reason,
                    'failure_reason', subitem_failure_reason,
                    'created_at', subitem_created_at
                )
                ORDER BY subitem_sort_order
            ) as subitems_json
        FROM checklist_subitems_data
        GROUP BY instance_item_id
    )

    -- Main query combining all data with JSON aggregation
    SELECT 
        -- Instance level information
        cid.instance_id,
        cid.template_id,
        cid.checklist_date,
        cid.shift,
        cid.shift_start,
        cid.shift_end,
        cid.instance_status,
        cid.created_at,
        cid.closed_at,
        cid.completion_time_seconds,
        cid.exception_count,
        cid.metadata,
        
        -- Template information
        cid.template_name,
        cid.template_description,
        cid.template_version,
        
        -- Section information
        cid.section_id,
        cid.section_name,
        
        -- User information
        cid.created_by,
        cid.created_by_name,
        cid.created_by_email,
        cid.closed_by,
        cid.closed_by_name,
        cid.closed_by_email,
        
        -- Aggregated items data as JSON
        (
            SELECT json_agg(
                json_build_object(
                    'item_id', ci.item_id,
                    'template_item_id', ci.template_item_id,
                    'template_item_key', ci.template_item_key,
                    'title', ci.template_item_title,
                    'description', ci.template_item_description,
                    'item_type', ci.template_item_type,
                    'is_required', ci.template_item_required,
                    'scheduled_time', ci.template_scheduled_time,
                    'notify_before_minutes', ci.notify_before_minutes,
                    'severity', ci.template_severity,
                    'sort_order', ci.template_sort_order,
                    'status', ci.item_status,
                    'completed_by', ci.completed_by,
                    'completed_by_name', ci.completed_by_name,
                    'completed_by_email', ci.completed_by_email,
                    'completed_at', ci.completed_at,
                    'skipped_reason', ci.skipped_reason,
                    'failure_reason', ci.failure_reason,
                    'subitems', COALESCE(sa.subitems_json, '[]'::json)
                )
                ORDER BY ci.template_sort_order
            )
            FROM checklist_items_data ci
            LEFT JOIN subitems_aggregated sa 
                ON ci.item_id = sa.instance_item_id
            WHERE ci.instance_id = cid.instance_id
        ) as items_data,
        
        -- Summary statistics
        (
            SELECT json_build_object(
                'total_items', COUNT(*),
                'completed_items', COUNT(*) FILTER (WHERE status = 'COMPLETED'),
                'pending_items', COUNT(*) FILTER (WHERE status = 'PENDING'),
                'skipped_items', COUNT(*) FILTER (WHERE status = 'SKIPPED'),
                'failed_items', COUNT(*) FILTER (WHERE status = 'FAILED'),
                'total_subitems', (
                    SELECT COUNT(*) 
                    FROM checklist_instance_subitems cis2
                    JOIN checklist_instance_items cii2 
                        ON cis2.instance_item_id = cii2.id
                    WHERE cii2.instance_id = cid.instance_id
                ),
                'completed_subitems', (
                    SELECT COUNT(*) 
                    FROM checklist_instance_subitems cis2
                    JOIN checklist_instance_items cii2 
                        ON cis2.instance_item_id = cii2.id
                    WHERE cii2.instance_id = cid.instance_id 
                      AND cis2.status = 'COMPLETED'
                )
            )
            FROM checklist_instance_items cii_stats
            WHERE cii_stats.instance_id = cid.instance_id
        ) as summary_statistics

    FROM checklist_instances_data cid
    WHERE cid.instance_id = %s
    ORDER BY cid.checklist_date DESC, cid.shift_start DESC;
    """
    
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, (instance_id,))
            result = cursor.fetchone()
            
            if result:
                # Convert RealDictRow to regular dict and handle JSON fields
                data = dict(result)
                
                # Parse JSON fields if they're strings
                if isinstance(data.get('items_data'), str):
                    data['items_data'] = json.loads(data['items_data'])
                if isinstance(data.get('summary_statistics'), str):
                    data['summary_statistics'] = json.loads(data['summary_statistics'])
                
                return data
            return None
    except Exception as e:
        logger.error(f"Database query error: {str(e)}")
        raise
    finally:
        conn.close()

# Import for io.BytesIO
import io
