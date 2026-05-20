import os
import json
import uuid
import psycopg2
from datetime import datetime
from minio import Minio

# Configurações MinIO
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ROOT_USER", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_ROOT_PASSWORD", "substitua-por-senha-segura")
MHTML_BUCKET_NAME = "inteligencia-aberta-mhtml"

minio_client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=False
)

def sync_orphans():
    try:
        # Conecta no Postgres
        conn = psycopg2.connect(
            dbname=os.getenv("POSTGRES_DB", "inteligencia_aberta"),
            user=os.getenv("POSTGRES_USER", "postgres"),
            password=os.getenv("POSTGRES_PASSWORD", "substitua-por-senha-segura"),
            host=os.getenv("POSTGRES_HOST", "db"),
            port=os.getenv("POSTGRES_PORT", "5432")
        )
        cursor = conn.cursor()
        
        # Pega o primeiro usuário
        cursor.execute("SELECT id FROM accounts_user ORDER BY date_joined ASC LIMIT 1;")
        user_row = cursor.fetchone()
        
        if not user_row:
            print("Nenhum usuário encontrado no banco.")
            return
            
        resolved_user_id = user_row[0]
        
        # Fallback de Organização (Tenant)
        cursor.execute("SELECT id FROM accounts_organization WHERE owner_id = %s LIMIT 1;", (resolved_user_id,))
        org_row = cursor.fetchone()
        
        if org_row:
            resolved_tenant_id = org_row[0]
        else:
            print("Criando organização individual padrão para o usuário...")
            resolved_tenant_id = str(uuid.uuid4())
            now = datetime.now()
            cursor.execute("""
                INSERT INTO accounts_organization (id, name, slug, org_type, owner_id, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (resolved_tenant_id, "Uso Individual", f"individual-{str(uuid.uuid4())[:8]}", "individual", resolved_user_id, now))
            conn.commit()

        # Pega todos os IDs que já estão no Postgres para não duplicar
        cursor.execute("SELECT id FROM artifacts_artifact;")
        existing_ids = {str(row[0]) for row in cursor.fetchall()}

        if minio_client.bucket_exists(MHTML_BUCKET_NAME):
            objects = minio_client.list_objects(MHTML_BUCKET_NAME)
            synced_count = 0
            
            for obj in objects:
                # O nome do objeto é "uuid.mhtml"
                artifact_id = obj.object_name.replace(".mhtml", "")
                
                if artifact_id not in existing_ids:
                    print(f"Sincronizando {obj.object_name}...")
                    
                    artifact_content = json.dumps({
                        "title": "Captura (Sincronizada do MinIO)",
                        "url": "Desconhecida",
                        "capture_timestamp": datetime.now().isoformat(),
                        "mhtml_bucket": MHTML_BUCKET_NAME,
                        "mhtml_path": obj.object_name
                    })
                    
                    now = datetime.now()
                    
                    insert_query = """
                        INSERT INTO artifacts_artifact (
                            id, artifact_type, content, classification_level, 
                            tenant_id, allow_external_llm, classified_by_id, 
                            classified_at, info_type, sources, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    
                    cursor.execute(insert_query, (
                        artifact_id, "documento", artifact_content, "restrito",
                        resolved_tenant_id, False, resolved_user_id, now, "fato", "[]", now, now
                    ))
                    
                    synced_count += 1
            
            conn.commit()
            print(f"Sincronização concluída! {synced_count} artefatos antigos registrados.")
        else:
            print("Bucket não existe.")
            
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Erro: {e}")

if __name__ == "__main__":
    sync_orphans()
