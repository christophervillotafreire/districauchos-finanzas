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
        gcp_service_account = st.secrets["gcp_service_account"]
        creds = service_account.Credentials.from_service_account_info(
            gcp_service_account, scopes=["https://www.googleapis.com/auth/drive"]
        )
        service = build('drive', 'v3', credentials=creds)
        folder_id = st.secrets["drive_folder_id"] 

        file_metadata = {'name': nombre_archivo, 'parents': [folder_id]}
        media = MediaIoBaseUpload(archivo_excel, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        
        file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        return True, file.get('id')
    except Exception as e:
        return False, str(e)

# --- LÓGICA DE PROCESAMIENTO ---
def procesar_archivos(archivo_excel, pct_comision):
    dfs = []
    
    try:
        # Leemos todas las hojas
        xls = pd.read_excel(archivo_excel, sheet_name=None, header=5)
        
        for nombre_hoja, df in xls.items():
            # 1. LIMPIEZA INICIAL
            # Verificamos columnas clave
            if 'Descripción' not in df.columns or 'Transferencia (+)' not in df.columns:
                continue
            
            # --- CORRECCIÓN DE TOTALES ---
            # Eliminamos filas que sean resúmenes del día (Totales, Utilidad, Saldos)
            # Convertimos a string para evitar errores
            df = df[~df['Descripción'].astype(str).str.contains("TOTAL", case=False, na=False)]
            df = df[~df['Descripción'].astype(str).str.contains("UTILIDAD", case=False, na=False)]
            df = df[~df['Descripción'].astype(str).str.contains("EFECTIVO EN CAJA", case=False, na=False)]
            df = df[~df['Descripción'].astype(str).str.contains("BASE DE CAJA", case=False, na=False)]
            
            # Llenamos vacíos con 0
            cols_dinero = ['Efectivo (+)', 'Transferencia (+)']
            df[cols_dinero] = df[cols_dinero].fillna(0)
            df['Descripción'] = df['Descripción'].fillna('')

            # Función para clasificar cada venta
            def clasificar_transaccion(fila):
                desc = str(fila['Descripción']).upper() # Todo a mayúsculas para facilitar búsqueda
                monto_transf = fila['Transferencia (+)']
                monto_efectivo = fila['Efectivo (+)']
                
                # A. CLASIFICACIÓN DE PAGO (Nequi / QR / Efectivo)
                tipo_pago = "Efectivo" # Por defecto
                
                # Prioridad: Si la descripción lo dice explícitamente
                if "NEQUI" in desc:
                    tipo_pago = "Nequi"
                elif "QR" in desc or "BANCOLOMBIA" in desc:
                    tipo_pago = "QR Bancolombia"
                elif monto_transf > 0: 
                    # Si hay dinero en transferencia pero no dice qué es, asumimos Transferencia
                    tipo_pago = "Transferencia (Otro)"
                
                # B. CLASIFICACIÓN DE EMPLEADO (%A, %J)
                empleado = "Sin Comision"
                # Buscamos %A, %J, %L, etc.
                match = re.search(r'%([A-Z])', desc)
                if match:
                    inicial = match.group(1)
                    if inicial == 'A': empleado = "Anderson (%A)"
                    elif inicial == 'J': empleado = "Jhosept (%J)"
                    else: empleado = f"Empleado %{inicial}"
                
                return pd.Series([tipo_pago, empleado])

            # Aplicamos la clasificación
            df[['Tipo_Pago', 'Empleado']] = df.apply(clasificar_transaccion, axis=1)
            
            # Solo guardamos si hay dinero involucrado (para no guardar filas vacías)
            df_con_dinero = df[(df['Efectivo (+)'] != 0) | (df['Transferencia (+)'] != 0)]
            
            if not df_con_dinero.empty:
                # Agregamos columna de fecha basada en la hoja (opcional, ayuda a auditar)
                df_con_dinero['Hoja_Origen'] = nombre_hoja
                dfs.append(df_con_dinero)
            
    except Exception as e:
        st.error(f"Error leyendo el Excel: {e}")
        return None

    if not dfs: return None
    
    # Tabla consolidada
    df_final = pd.concat(dfs, ignore_index=True)
    
    # Calculamos columna Total Dinero (Efectivo + Transf)
    df_final['Total Venta'] = df_final['Efectivo (+)'] + df_final['Transferencia (+)']
    
    # Calculamos la Comisión
    df_final['Comisión Calculada'] = 0
    mask_comision = df_final['Empleado'] != "Sin Comision"
    df_final.loc[mask_comision, 'Comisión Calculada'] = df_final.loc[mask_comision, 'Total Venta'] * (pct_comision / 100)
    
    return df_final

# --- INTERFAZ GRÁFICA ---
st.set_page_config(page_title="Finanzas Districauchos", page_icon="💰")

st.title("📊 Finanzas Districauchos")
st.markdown("---")

col1, col2 = st.columns(2)
with col1:
    archivo = st.file_uploader("📂 Cargar Excel Mensual", type=['xlsx'])
with col2:
    st.info("Configuración de Comisiones")
    porcentaje = st.number_input("Porcentaje de Comisión (%)", min_value=0, max_value=100, value=15)

if archivo:
    if st.button("🚀 Procesar Datos y Subir a Drive", type="primary"):
        with st.spinner('Analizando hoja por hoja...'):
            df_completo = procesar_archivos(archivo, porcentaje)
        
        if df_completo is not None:
            # 1. RESUMEN DE VENTAS (Nequi vs QR)
            st.subheader("💰 Resumen de Dineros")
            resumen_pago = df_completo.groupby('Tipo_Pago')[['Efectivo (+)', 'Transferencia (+)']].sum()
            # Total general
            resumen_pago['Total Global'] = resumen_pago['Efectivo (+)'] + resumen_pago['Transferencia (+)']
            st.dataframe(resumen_pago.style.format("${:,.0f}"))

            # 2. RESUMEN DE COMISIONES (Lo que pediste de %A y %J)
            st.subheader(f"👷 Liquidación de Comisiones ({porcentaje}%)")
            
            # Filtramos solo empleados
            df_emp = df_completo[df_completo['Empleado'] != "Sin Comision"]
            
            if not df_emp.empty:
                resumen_emp = df_emp.groupby('Empleado').agg(
                    Total_Trabajos=('Total Venta', 'sum'),
                    Comision_A_Pagar=('Comisión Calculada', 'sum')
                )
                st.dataframe(resumen_emp.style.format("${:,.0f}"))
            else:
                st.warning("No se encontraron ventas con etiquetas %A o %J")

            # 3. SUBIR A DRIVE
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df_completo.to_excel(writer, index=False, sheet_name='Detallado_Ventas')
                resumen_pago.to_excel(writer, sheet_name='Resumen_Pagos')
                if not df_emp.empty:
                    resumen_emp.to_excel(writer, sheet_name='Resumen_Comisiones')
            
            buffer.seek(0)
            fecha_hoy = datetime.now().strftime("%Y-%m-%d_%H-%M")
            nombre_archivo = f"Consolidado_Districauchos_{fecha_hoy}.xlsx"
            
            st.markdown("---")
            st.write("☁️ Subiendo a Google Drive...")
            exito, mensaje = subir_a_drive(buffer, nombre_archivo)
            
            if exito:
                st.success(f"✅ ¡Guardado Exitoso! ID Archivo: {mensaje}")
            else:
                st.error(f"❌ Error subiendo a Drive: {mensaje}")
                st.warning("Revisa que el ID de la carpeta en 'Secrets' sea correcto y que el 'robot' tenga permiso de Editor en esa carpeta.")
