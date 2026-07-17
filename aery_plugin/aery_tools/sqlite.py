import sqlite3
import os

class SqliteReaderTool:
    """Aery-Style tool for querying SQLite/GeoPackage databases."""
    
    name = "sqlite_reader"
    description = "Execute a read-only SQL query against a SQLite or GeoPackage (.gpkg) file."
    
    def execute(self, params: dict) -> dict:
        db_path = params.get("db_path", "")
        query = params.get("query", "")
        
        if not db_path or not query:
            return {"type": "text", "text": "Error: db_path and query are required."}
            
        if not os.path.exists(db_path):
            return {"type": "text", "text": f"Error: Database file not found at {db_path}"}
            
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute(query)
            
            # Fetch column names
            columns = [description[0] for description in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            
            # Format output
            output = f"Columns: {', '.join(columns)}\n"
            for row in rows[:50]: # Limit to 50 rows to save context
                output += f"{row}\n"
                
            if len(rows) > 50:
                output += f"\n...and {len(rows) - 50} more rows."
                
            conn.close()
            return {"type": "text", "text": output}
        except Exception as e:
            return {"type": "text", "text": f"SQLite Error: {str(e)}"}
