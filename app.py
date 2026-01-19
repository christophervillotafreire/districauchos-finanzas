import streamlit as st
import pandas as pd
import re
import io
from datetime import datetime

# --- LÓGICA DE PROCESAMIENTO ---
def procesar_archivos(archivo_excel, pct_comision):
    dfs = []
    
    try:
        # Leemos todas las hojas del Excel
        xls = pd.read_excel(archivo_excel, sheet_name=None, header=5)
        
        for nombre_hoja, df in xls.items():
            # 1. VALIDACIÓN BÁSICA
            # Si la hoja no tiene las columnas de dinero, la saltamos (ej: hojas de resumen vacías)
            if 'Descripción' not in df.columns or 'Transferencia (+)' not in df.columns:
                continue
            
            # --- FILTRO MAESTRO (Para eliminar filas basura y totales duplicados) ---
            # Convertimos a texto y mayúsculas para buscar palabras clave
            filtro = df['Descripción'].astype(str).str.upper()
            
            # Solo mantenemos las filas que NO tengan estas palabras:
            df = df[~filtro.str.contains("TOTAL", na=False)]
            df = df[~filtro.str.contains("UTILIDAD", na=False)]
            df = df[~filtro.str.contains("EFECTIVO EN CAJA", na=False)]
            df = df[~filtro.str.contains("BASE DE CAJA", na=False)]
            df = df[~filtro.str.contains("EGRESOS", na=False)]
            df = df[~filtro.str.contains("RESUMEN", na=False)]
            df = df[~filtro.str.contains("SALDO", na=False)]
            
            # 2. LIMPIEZA DE NÚMEROS
            cols_dinero = ['Efectivo (+)', 'Transferencia (+)']
            for col in cols_dinero:
                # Forzamos a que sean números (si hay texto, lo pone en 0)
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
                
            df['Descripción'] = df['Descripción'].fillna('')

            # 3. FUNCIÓN DE CLASIFICACIÓN (Detecta Nequi, QR y Empleados)
            def clasificar_transaccion(fila):
                desc = str(fila['Descripción']).upper() 
                monto_transf = fila['Transferencia (+)']
                monto_efectivo = fila['Efectivo (+)']
                
                # --- A. TIPO DE PAGO ---
                tipo_pago = "Efectivo" # Valor por defecto
                
                # Si dice explícitamente el medio de pago en la descripción
                if "NEQUI" in desc:
                    tipo_pago = "Nequi"
                elif "QR" in desc or "BANCOLOMBIA" in desc:
                    tipo_pago = "QR Bancolombia"
                elif monto_transf > 0: 
                    # Si hay plata en columna transferencia pero no dice qué es
                    tipo_pago = "Transferencia (Otro)"
                
                # --- B. EMPLEADO (%A, %J) ---
                empleado = "Sin Comision"
                # Busca el patrón %LETRA (Ej: %A, %J, %P)
                match = re.search(r'%([A-Z])', desc)
                if match:
                    inicial = match.group(1)
                    if inicial == 'A': empleado = "Anderson (%A)"
                    elif inicial == 'J': empleado = "Jhosept (%J)"
                    else: empleado = f"Empleado %{inicial}"
                
                return pd.Series([tipo_pago, empleado])

            # Aplicamos la clasificación fila por fila
            df[['Tipo_Pago', 'Empleado']] = df.apply(clasificar_transaccion, axis=1)
            
            # 4. FILTRO FINAL
            # Solo guardamos filas que tengan dinero real (mayor a 0)
            df_con_dinero = df[(df['Efectivo (+)'] > 0) | (df['Transferencia (+)'] > 0)]
            
            if not df_con_dinero.empty:
                # Agregamos una columna para saber de qué día vino el dato
                df_con_dinero['Fecha_Origen'] = nombre_hoja
                dfs.append(df_con_dinero)
            
    except Exception as e:
        st.error(f"Error procesando el archivo: {e}")
        return None

    if not dfs: return None
    
    # Unimos todas las hojas limpias en una sola tabla
    df_final = pd.concat(dfs, ignore_index=True)
    
    # Columna Total (Suma de las dos columnas)
    df_final['Total Venta'] = df_final['Efectivo (+)'] + df_final['Transferencia (+)']
    
    # Calculamos la Comisión Dinámica
    df_final['Comisión Calculada'] = 0
    mask_comision = df_final['Empleado'] != "Sin Comision"
    # Fórmula: Venta * (Porcentaje / 100)
    df_final.loc[mask_comision, 'Comisión Calculada'] = df_final.loc[mask_comision, 'Total Venta'] * (pct_comision / 100.0)
    
    return df_final

# --- INTERFAZ GRÁFICA ---
st.set_page_config(page_title="Finanzas Districauchos", page_icon="💰", layout="centered")

st.title("📊 Finanzas Districauchos")
st.write("Herramienta de consolidación y cálculo de comisiones.")
st.markdown("---")

# Panel de Configuración
col1, col2 = st.columns([1, 1])
with col1:
    archivo = st.file_uploader("📂 Cargar Excel Mensual", type=['xlsx'])
with col2:
    st.info("⚙️ Configuración")
    # Selector de porcentaje numérico
    porcentaje = st.number_input("Porcentaje de Comisión (%)", min_value=0, max_value=100, value=15)

if archivo:
    if st.button("🚀 Procesar Datos", type="primary"):
        with st.spinner('Leyendo 31 hojas y limpiando totales...'):
            df_completo = procesar_archivos(archivo, porcentaje)
        
        if df_completo is not None:
            st.success("✅ ¡Procesamiento Exitoso!")
            
            # --- SECCIÓN 1: RESUMEN DE DINEROS ---
            st.subheader("💰 Resumen Real (Sin Duplicados)")
            # Agrupamos por tipo de pago
            resumen_pago = df_completo.groupby('Tipo_Pago')[['Efectivo (+)', 'Transferencia (+)']].sum()
            # Totalizamos filas
            resumen_pago['Total Global'] = resumen_pago['Efectivo (+)'] + resumen_pago['Transferencia (+)']
            
            # Mostramos tabla con formato de pesos
            st.dataframe(resumen_pago.style.format("${:,.0f}"))
            
            # --- SECCIÓN 2: COMISIONES ---
            st.subheader(f"👷 Liquidación de Comisiones ({porcentaje}%)")
            
            # Filtramos solo lo que tiene empleado asignado
            df_emp = df_completo[df_completo['Empleado'] != "Sin Comision"]
            
            if not df_emp.empty:
                # Resumen por empleado
                resumen_emp = df_emp.groupby('Empleado').agg(
                    Ventas_Totales=('Total Venta', 'sum'),
                    Comision_A_Pagar=('Comisión Calculada', 'sum')
                ).sort_values('Comision_A_Pagar', ascending=False)
                
                st.dataframe(resumen_emp.style.format("${:,.0f}"))
            else:
                st.warning("⚠️ No se detectaron ventas con etiquetas %A o %J en las descripciones.")

            # --- SECCIÓN 3: DESCARGA ---
            st.markdown("---")
            st.subheader("📥 Descargar Resultados")
            
            # Generamos el Excel en memoria
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df_completo.to_excel(writer, index=False, sheet_name='Detalle_Todas_Ventas')
                resumen_pago.to_excel(writer, sheet_name='Resumen_Dineros')
                if not df_emp.empty:
                    resumen_emp.to_excel(writer, sheet_name='Liquidacion_Comisiones')
            
            buffer.seek(0)
            fecha_hoy = datetime.now().strftime("%Y-%m-%d")
            
            # BOTÓN DE DESCARGA
            st.download_button(
                label="💾 Descargar Excel Consolidado",
                data=buffer,
                file_name=f"Consolidado_Districauchos_{fecha_hoy}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary"
            )
