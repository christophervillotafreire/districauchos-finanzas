import streamlit as st
import pandas as pd
import re
import io
from datetime import datetime

# --- LÓGICA DE PROCESAMIENTO ---
def procesar_archivos(archivo_excel, pct_comision):
    dfs = []
    
    try:
        # LEER DESDE LA FILA 6: header=5 significa que la fila 6 contiene los títulos (Descripción, Efectivo, etc.)
        # Esto ignora automáticamente las primeras 5 filas de encabezado basura.
        xls = pd.read_excel(archivo_excel, sheet_name=None, header=5)
        
        for nombre_hoja, df in xls.items():
            # 1. VALIDACIÓN: Si no existe la columna "Descripción", saltamos la hoja
            if 'Descripción' not in df.columns:
                continue
            
            # --- FILTRO MAESTRO (Limpieza de filas totales) ---
            # Convertimos la columna A (Descripción) a texto y mayúsculas
            columna_a = df['Descripción'].astype(str).str.upper()
            
            # Eliminamos filas que contengan "TOTAL", "UTILIDAD", etc.
            df = df[~columna_a.str.contains("TOTAL", na=False)]
            df = df[~columna_a.str.contains("UTILIDAD", na=False)]
            df = df[~columna_a.str.contains("EFECTIVO EN CAJA", na=False)]
            df = df[~columna_a.str.contains("BASE DE CAJA", na=False)]
            
            # 2. LIMPIEZA DE NÚMEROS (Columnas de dinero)
            # Aunque buscamos texto en la A, necesitamos sumar las columnas de dinero
            cols_dinero = ['Efectivo (+)', 'Transferencia (+)']
            for col in cols_dinero:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
                else:
                    df[col] = 0 # Si no existe la columna, ponemos 0
            
            df['Descripción'] = df['Descripción'].fillna('')

            # --- 3. CLASIFICACIÓN ESTRICTA (LO QUE PEDISTE) ---
            def clasificar_transaccion(fila):
                # Solo miramos la columna A (Descripción)
                texto_columna_a = str(fila['Descripción']).upper() 
                
                # Dinero asociado a esta fila
                monto_transf = fila['Transferencia (+)']
                
                # LÓGICA DE PAGO
                tipo_pago = "Efectivo" # Por defecto
                
                # Solo marcamos como QR o Nequi si la palabra está explícita en Columna A
                if "NEQUI" in texto_columna_a:
                    tipo_pago = "Nequi"
                elif "QR" in texto_columna_a or "BANCOLOMBIA" in texto_columna_a:
                    tipo_pago = "QR Bancolombia"
                elif monto_transf > 0:
                    # Si hay dinero en transferencia pero NO dice Nequi ni QR,
                    # lo llamamos simplemente "Transferencia (Sin identificar)"
                    # OJO: NO usamos la palabra "Transferencia" para buscar, solo el monto.
                    tipo_pago = "Transferencia (Sin identificar)"
                
                # LÓGICA DE EMPLEADO (%A, %J)
                empleado = "Sin Comision"
                match = re.search(r'%([A-Z])', texto_columna_a)
                if match:
                    inicial = match.group(1)
                    if inicial == 'A': empleado = "Anderson (%A)"
                    elif inicial == 'J': empleado = "Jhosept (%J)"
                    else: empleado = f"Empleado %{inicial}"
                
                return pd.Series([tipo_pago, empleado])

            # Aplicamos la clasificación
            df[['Tipo_Pago', 'Empleado']] = df.apply(clasificar_transaccion, axis=1)
            
            # 4. FILTRO FINAL: Solo guardamos filas que tengan dinero
            df_con_dinero = df[(df['Efectivo (+)'] > 0) | (df['Transferencia (+)'] > 0)]
            
            if not df_con_dinero.empty:
                df_con_dinero['Fecha_Origen'] = nombre_hoja
                dfs.append(df_con_dinero)
            
    except Exception as e:
        st.error(f"Error procesando el archivo: {e}")
        return None

    if not dfs: return None
    
    # Consolidación
    df_final = pd.concat(dfs, ignore_index=True)
    df_final['Total Venta'] = df_final['Efectivo (+)'] + df_final['Transferencia (+)']
    
    # Cálculos
    df_final['Comisión Calculada'] = 0
    mask_comision = df_final['Empleado'] != "Sin Comision"
    df_final.loc[mask_comision, 'Comisión Calculada'] = df_final.loc[mask_comision, 'Total Venta'] * (pct_comision / 100.0)
    
    return df_final

# --- INTERFAZ GRÁFICA ---
st.set_page_config(page_title="Finanzas Districauchos", page_icon="📱", layout="centered")

# Truco visual para que se vea bien en celular
st.markdown("""
    <style>
    .stButton>button { width: 100%; border-radius: 10px; height: 3em; font-weight: bold;}
    </style>
    """, unsafe_allow_html=True)

st.title("📱 Finanzas Districauchos")
st.write("Sube el Excel mensual para liquidar.")

col1, col2 = st.columns([1, 1])
with col1:
    archivo = st.file_uploader("📂 Excel", type=['xlsx'])
with col2:
    porcentaje = st.number_input("% Comisión", value=15)

if archivo:
    if st.button("🚀 Calcular Todo", type="primary"):
        with st.spinner('Procesando...'):
            df_completo = procesar_archivos(archivo, porcentaje)
        
        if df_completo is not None:
            # 1. TABLA RESUMEN PAGOS
            st.success("✅ ¡Listo!")
            st.subheader("💰 Resumen por Medio de Pago")
            resumen_pago = df_completo.groupby('Tipo_Pago')[['Efectivo (+)', 'Transferencia (+)']].sum()
            resumen_pago['Total'] = resumen_pago['Efectivo (+)'] + resumen_pago['Transferencia (+)']
            st.dataframe(resumen_pago.style.format("${:,.0f}"))

            # 2. TABLA COMISIONES
            st.subheader(f"👷 Comisiones ({porcentaje}%)")
            df_emp = df_completo[df_completo['Empleado'] != "Sin Comision"]
            if not df_emp.empty:
                resumen_emp = df_emp.groupby('Empleado').agg(
                    Vendido=('Total Venta', 'sum'),
                    Pagar=('Comisión Calculada', 'sum')
                )
                st.dataframe(resumen_emp.style.format("${:,.0f}"))
            else:
                st.info("No hay comisiones este mes.")

            # 3. DESCARGAR
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df_completo.to_excel(writer, index=False, sheet_name='Detalle')
                resumen_pago.to_excel(writer, sheet_name='Resumen_Pagos')
                if not df_emp.empty:
                    resumen_emp.to_excel(writer, sheet_name='Comisiones')
            buffer.seek(0)
            
            fecha = datetime.now().strftime("%Y-%m-%d")
            st.download_button(
                label="📥 Descargar Excel Resultado",
                data=buffer,
                file_name=f"Resultado_Districauchos_{fecha}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary"
            )
