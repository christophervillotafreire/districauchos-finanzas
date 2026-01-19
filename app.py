import streamlit as st
import pandas as pd
import re
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io
from datetime import datetime

# --- CONFIGURACIÓN DE DRIVE ---
def subir_a_drive(archivo_excel, nombre_archivo):
    try:
        # Recuperamos la info secreta desde Streamlit Secrets
        gcp_service_account = st.secrets["gcp_service_account"]
        creds = service_account.Credentials.from_service_account_info(
            gcp_service_account, scopes=["https://www.googleapis.com/auth/drive"]
        )
        service = build('drive', 'v3', credentials=creds)

        # ID de la carpeta
        folder_id = st.secrets["drive_folder_id"] 

        file_metadata = {'name': nombre_archivo, 'parents': [folder_id]}
        media = MediaIoBaseUpload(archivo_excel, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        
        file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        return True
    except Exception as e:
        st.error(f"Error subiendo a Drive: {e}")
        return False

# --- LÓGICA DE PROCESAMIENTO ---
def procesar_archivos(archivo_excel):
    dfs = []
    try:
        # Leer TODAS las hojas (sheet_name=None) y saltar encabezados (header=5)
        xls = pd.read_excel(archivo_excel, sheet_name=None, header=5)
        
        for nombre_hoja, df in xls.items():
            # Filtro: Solo procesar hojas que tengan estructura de ventas
            if 'Descripción' not in df.columns or 'Transferencia (+)' not in df.columns:
                continue

            # Limpieza
            cols_dinero = ['Efectivo (+)', 'Transferencia (+)']
            df[cols_dinero] = df[cols_dinero].fillna(0)
            df['Descripción'] = df['Descripción'].fillna('')

            # Función interna de clasificación
            def clasificar_transaccion(fila):
                desc = str(fila['Descripción']).lower()
                monto_transf = fila['Transferencia (+)']
                etiqueta_pago = "Efectivo"
                
                # 1. Tipo de Pago
                if monto_transf > 0:
                    if 'nequi' in desc: etiqueta_pago = 'Nequi'
                    elif 'qr' in desc or 'bancolombia' in desc: etiqueta_pago = 'QR Bancolombia'
                    else: etiqueta_pago = 'Transferencia (Otro)'
                
                # 2. Empleado (%)
                empleados = re.findall(r'%(\w+)', str(fila['Descripción']))
                empleado_str = ", ".join(empleados) if empleados else "Sin Comision"

                return pd.Series([etiqueta_pago, empleado_str])

            df[['Tipo_Pago', 'Empleado']] = df.apply(clasificar_transaccion, axis=1)
            dfs.append(df)
            
    except Exception as e:
        st.error(f"Error procesando el Excel: {e}")
        return None

    if not dfs: return None
    return pd.concat(dfs, ignore_index=True)

# --- INTERFAZ GRÁFICA ---
st.title("📊 Finanzas Districauchos")
st.write("Sube el Excel mensual (con todas las hojas diarias).")

archivo = st.file_uploader("Cargar Excel del Mes", type=['xlsx'])

if archivo:
    if st.button("Procesar y Subir a Drive"):
        with st.spinner('Procesando días...'):
            df_completo = procesar_archivos(archivo)
        
        if df_completo is not None:
            # 1. Resumen Pagos
            st.subheader("💰 Resumen por Tipo de Pago")
            resumen_pago = df_completo.groupby('Tipo_Pago')[['Efectivo (+)', 'Transferencia (+)']].sum().sum(axis=1)
            st.dataframe(resumen_pago)

            # 2. Resumen Empleados
            st.subheader("👷 Comisiones (Etiqueta %)")
            df_comisiones = df_completo[df_completo['Empleado'] != "Sin Comision"].copy()
            if not df_comisiones.empty:
                df_comisiones['Empleado'] = df_comisiones['Empleado'].str.split(", ")
                df_comisiones = df_comisiones.explode('Empleado')
                resumen_empleados = df_comisiones.groupby('Empleado')[['Efectivo (+)', 'Transferencia (+)']].sum().sum(axis=1)
                st.dataframe(resumen_empleados)
            else:
                st.info("No hay ventas con etiqueta %Empleado")

            # 3. Preparar y Subir
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df_completo.to_excel(writer, index=False, sheet_name='Detallado')
                resumen_pago.to_excel(writer, sheet_name='Resumen_Pagos')
            buffer.seek(0)
            
            fecha_hoy = datetime.now().strftime("%Y-%m-%d_%H-%M")
            nombre_archivo = f"Consolidado_Districauchos_{fecha_hoy}.xlsx"
            
            if subir_a_drive(buffer, nombre_archivo):
                st.success(f"✅ ¡Éxito! Archivo guardado en Drive: {nombre_archivo}")
            else:
                st.error("⚠️ Error subiendo a Drive. Revisa los Secrets.")
