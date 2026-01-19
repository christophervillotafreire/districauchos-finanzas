import streamlit as st
import pandas as pd
import re
import io
from datetime import datetime

# --- LÓGICA DE PROCESAMIENTO ---
def procesar_archivos(archivo_excel, pct_comision):
    dfs = []
    
    try:
        # LEER DESDE LA FILA 6: header=5 (índice 0) implica que la fila 6 del Excel son los títulos.
        xls = pd.read_excel(archivo_excel, sheet_name=None, header=5)
        
        for nombre_hoja, df in xls.items():
            # 1. VALIDACIÓN BÁSICA
            # Normalizamos nombres de columnas (quitamos espacios extra por si acaso)
            df.columns = df.columns.str.strip()
            
            if 'Descripción' not in df.columns:
                continue
            
            # Aseguramos que exista la columna "Tipo" para evitar errores, si no existe la creamos vacía
            if 'Tipo' not in df.columns:
                df['Tipo'] = ''

            # --- 2. FILTRO MAESTRO (Limpieza de filas basura) ---
            # Convertimos columnas de texto a mayúsculas para facilitar búsqueda
            df['Descripción'] = df['Descripción'].astype(str).str.upper().fillna('')
            df['Tipo'] = df['Tipo'].astype(str).str.upper().fillna('')
            
            # Palabras prohibidas (Filas de totales o balances)
            palabras_a_eliminar = [
                "TOTAL", "EFECTIVO", "UTILIDAD"
            ]
            
            # Filtramos: Nos quedamos solo con las filas que NO contengan esas palabras en Descripción
            pattern = '|'.join(palabras_a_eliminar)
            df = df[~df['Descripción'].str.contains(pattern, na=False)]
            
            # --- 3. LIMPIEZA DE NÚMEROS ---
            cols_dinero = ['Efectivo (+)', 'Transferencia (+)']
            for col in cols_dinero:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
                else:
                    df[col] = 0 
            
            # --- 4. CLASIFICACIÓN AVANZADA (A + B) ---
            def clasificar_transaccion(fila):
                # Texto combinado para buscar en ambos lados al mismo tiempo
                # Usamos un set o simplemente buscamos en las variables individuales
                desc = fila['Descripción'] # Ya está en mayúsculas
                tipo = fila['Tipo']        # Ya está en mayúsculas
                
                monto_transf = fila['Transferencia (+)']
                
                # LÓGICA DE PAGO (Jerárquica)
                # 1. Buscamos NEQUI en A o en B
                if "NEQUI" in desc or "NEQUI" in tipo:
                    metodo = "Nequi"
                
                # 2. Buscamos QR o BANCOLOMBIA en A o en B
                elif ("QR" in desc or "BANCOLOMBIA" in desc) or ("QR" in tipo or "BANCOLOMBIA" in tipo):
                    metodo = "QR Bancolombia"
                
                # 3. Si no es lo anterior, pero tiene valor en la columna Transferencia
                elif monto_transf > 0:
                    metodo = "Transferencia (Otro)"
                
                # 4. Por descarte, asumimos Efectivo
                else:
                    metodo = "Efectivo"
                
                # LÓGICA DE EMPLEADO (%A, %J en Descripción)
                empleado = "Sin Comision"
                match = re.search(r'%([A-Z])', desc)
                if match:
                    inicial = match.group(1)
                    if inicial == 'A': empleado = "Anderson (%A)"
                    elif inicial == 'J': empleado = "Jhosept (%J)"
                    else: empleado = f"Empleado %{inicial}"
                
                return pd.Series([metodo, empleado])

            # Aplicamos la clasificación fila por fila
            df[['Tipo_Pago', 'Empleado']] = df.apply(clasificar_transaccion, axis=1)
            
            # --- 5. FILTRO FINAL ---
            # Solo guardamos filas que tengan dinero real sumado
            df_con_dinero = df[(df['Efectivo (+)'] > 0) | (df['Transferencia (+)'] > 0)]
            
            if not df_con_dinero.empty:
                df_con_dinero['Fecha_Origen'] = nombre_hoja
                dfs.append(df_con_dinero)
            
    except Exception as e:
        st.error(f"Error procesando el archivo. Revisa que el formato sea correcto. Detalle: {e}")
        return None

    if not dfs: return None
    
    # Consolidación final
    df_final = pd.concat(dfs, ignore_index=True)
    df_final['Total Venta'] = df_final['Efectivo (+)'] + df_final['Transferencia (+)']
    
    # Cálculos de comisión
    df_final['Comisión Calculada'] = 0
    mask_comision = df_final['Empleado'] != "Sin Comision"
    # Calculamos comisión sobre el total de la venta (Efectivo + Transf)
    df_final.loc[mask_comision, 'Comisión Calculada'] = df_final.loc[mask_comision, 'Total Venta'] * (pct_comision / 100.0)
    
    return df_final

# --- INTERFAZ GRÁFICA (STREAMLIT) ---
st.set_page_config(page_title="Finanzas Districauchos", page_icon="📱", layout="centered")

# Estilos CSS para móviles
st.markdown("""
    <style>
    .stButton>button { width: 100%; border-radius: 10px; height: 3em; font-weight: bold;}
    div[data-testid="stMetricValue"] { font-size: 1.2rem; }
    </style>
    """, unsafe_allow_html=True)

st.title("📱 Finanzas Districauchos v2.0")
st.markdown("Liquidación mensual con búsqueda en columnas **Descripción** y **Tipo**.")

# Inputs
col1, col2 = st.columns([1, 1])
with col1:
    archivo = st.file_uploader("📂 Cargar Excel Mensual", type=['xlsx'])
with col2:
    porcentaje = st.number_input("% Comisión Empleados", value=15, step=1)

# Botón de Acción
if archivo:
    if st.button("🚀 Procesar Contabilidad", type="primary"):
        with st.spinner('Analizando hoja por hoja...'):
            df_completo = procesar_archivos(archivo, porcentaje)
        
        if df_completo is not None:
            st.success("✅ Procesamiento exitoso")
            
            # --- 1. RESUMEN DE PAGOS ---
            st.subheader("💰 Dinero por Medio de Pago")
            # Agrupamos por tipo de pago
            resumen_pago = df_completo.groupby('Tipo_Pago')[['Efectivo (+)', 'Transferencia (+)']].sum()
            # Creamos columna totalizadora
            resumen_pago['Total Recibido'] = resumen_pago['Efectivo (+)'] + resumen_pago['Transferencia (+)']
            
            # Mostramos tabla con formato moneda
            st.dataframe(resumen_pago.style.format("${:,.0f}"), use_container_width=True)

            # --- 2. LIQUIDACIÓN EMPLEADOS ---
            st.subheader(f"👷 Liquidación Comisiones ({porcentaje}%)")
            df_emp = df_completo[df_completo['Empleado'] != "Sin Comision"]
            
            if not df_emp.empty:
                resumen_emp = df_emp.groupby('Empleado').agg(
                    Ventas_Totales=('Total Venta', 'sum'),
                    A_Pagar=('Comisión Calculada', 'sum')
                )
                st.dataframe(resumen_emp.style.format("${:,.0f}"), use_container_width=True)
            else:
                st.info("No se detectaron ventas con código de empleado (%A o %J).")

            # --- 3. EXPORTAR RESULTADOS ---
            st.divider()
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                # Hoja 1: Data cruda (Detalle)
                df_completo.to_excel(writer, index=False, sheet_name='Detalle_Completo')
                # Hoja 2: Resumen Pagos
                resumen_pago.to_excel(writer, sheet_name='Resumen_Pagos')
                # Hoja 3: Comisiones
                if not df_emp.empty:
                    resumen_emp.to_excel(writer, sheet_name='Liquidacion_Comisiones')
            
            buffer.seek(0)
            fecha_hoy = datetime.now().strftime("%Y-%m-%d")
            
            st.download_button(
                label="📥 Descargar Reporte Final (.xlsx)",
                data=buffer,
                file_name=f"Reporte_Districauchos_{fecha_hoy}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary"
            )
