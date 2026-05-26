# Propuesta Tecnica: Dimed-LIS
## Sistema de Informacion de Laboratorio con Facturacion Electronica

**Preparado por:** Dimed Healthcare Technology
**Fecha:** Mayo 2026
**Version:** 1.0

---

## 1. Presentacion

**Dimed-LIS** es un sistema integral diseñado especificamente para laboratorios clinicos. Combina la gestion completa de procesos de laboratorio (muestras, resultados, control de calidad) con un modulo de facturacion electronica totalmente integrado con el SRI.

El sistema es una aplicacion web moderna que funciona desde cualquier navegador, sin necesidad de instalar software adicional en las estaciones de trabajo.

### Ventajas Principales

- **Especializado para laboratorios** — No es un sistema hospitalario generico adaptado; esta diseñado desde cero para el flujo de trabajo de un laboratorio clinico
- **Facturacion electronica SRI** — Emision, firma digital, envio y autorizacion de comprobantes electronicos integrados
- **Trazabilidad completa** — Desde la toma de muestra hasta la entrega de resultados al paciente
- **Control de calidad** — Graficos de Levey-Jennings, control por lote y analizador
- **Multi-sede** — Soporte para multiples sucursales con consolidacion de datos
- **Seguridad** — Roles diferenciados, auditoria de acciones, datos encriptados

---

## 2. Modulo LIS — Laboratorio

### 2.1 Catalogo de Pruebas

- Catalogo configurable con 80+ pruebas pre-cargadas
- Categorias: hematologia, quimica clinica, coagulacion, hormonas, inmunologia, microbiologia, urinalisis, marcadores tumorales, gases arteriales
- Precios configurables por prueba
- Perfiles de pruebas (paquetes): ej. "Perfil Hepatico" = TGO + TGP + Bilirrubinas + FA + GGT
- Activacion/desactivacion de pruebas segun disponibilidad

### 2.2 Gestion de Muestras

- Registro de muestras con codigo unico y codigo de barras
- Tipos de muestra: sangre, orina, heces, liquidos, tejidos
- Trazabilidad completa: quien tomo la muestra, cuando, donde
- Estados: recolectada → recibida → en proceso → completada
- Rechazo de muestras con motivo documentado
- Busqueda por paciente, fecha, estado

### 2.3 Ingreso de Resultados

- **Ingreso manual** — Interfaz rapida por muestra con todos los analitos
- **Interfaz con analizadores** — Recepcion automatica de resultados via protocolo HL7 (Mirth Connect)
  - Soporta: HL7 MLLP, archivo, serial, manual
  - Compatible con analizadores de las principales marcas
- Comparacion automatica con rangos de referencia
- Alertas de valores criticos (codigo de colores)
- Rangos de referencia por edad y sexo

### 2.4 Validacion Doble

- **Validacion tecnica** — El laboratorista revisa y confirma los resultados
- **Validacion medica** — El bioquimico/patologo aprueba los resultados finales
- Registro de quien valido y cuando (auditoria completa)
- Validacion individual o masiva por muestra

### 2.5 Control de Calidad

- Registro de controles por analizador y analito
- Niveles: bajo, normal, alto
- Graficos de Levey-Jennings
- Calculo automatico de SD y CV%
- Alertas de control fuera de rango
- Cumplimiento ISO 15189

### 2.6 Gestion de Equipos

- Registro de analizadores con datos tecnicos
- Configuracion de conexion (IP, puerto, protocolo)
- Estado activo/inactivo
- Historial de control de calidad por equipo

### 2.7 Reportes de Resultados

- Impresion de resultados por paciente
- Envio por correo electronico al paciente
- Portal web para consulta de resultados (opcional)
- Estadisticas de produccion: muestras/dia, tasa de rechazo, tiempo de respuesta

---

## 3. Modulo de Facturacion

### 3.1 Caja y Recepcion

- Registro rapido de pacientes
- Seleccion de pruebas desde catalogo con precios
- Calculo automatico de totales
- Metodos de pago: efectivo, tarjeta de debito, tarjeta de credito, transferencia bancaria
- Impresion de comprobante de pago
- Arqueo de caja por turno

### 3.2 Facturacion

- Creacion de facturas desde ordenes de laboratorio
- Flujo: borrador → validada → contabilizada → pagada
- Numeracion secuencial automatica (formato SRI)
- Nota de credito para anulaciones
- Busqueda y filtros por fecha, paciente, estado

### 3.3 Facturacion Electronica SRI

- **Generacion XML** — Estructura conforme a esquema SRI v2.1
- **Firma electronica** — Firma digital con certificado .p12
- **Clave de acceso** — Generacion automatica de 49 digitos
- **Envio automatico** — Al web service del SRI (ambientes pruebas y produccion)
- **RIDE** — Generacion de PDF con codigo de barras de clave de acceso
- **Nota de credito electronica** — Vinculada a factura original
- **ATS** — Generacion del Anexo Transaccional Simplificado mensual
- **Reenvio automatico** — Reintentos en caso de fallo de comunicacion

### 3.4 Cuentas por Cobrar

- Saldos por paciente y por aseguradora
- Envejecimiento de cartera: corriente, 30, 60, 90+ dias
- Aplicacion de pagos parciales y totales
- Resumen consolidado de CxC

### 3.5 Reportes Financieros

- Ventas diarias (detallado y resumen)
- Ingresos por categoria de prueba
- Estado de cuentas por cobrar
- Balance de comprobacion
- Estado de resultados

---

## 4. Modulo de Seguros (Opcional)

### 4.1 Gestion de Aseguradoras

- Catalogo de aseguradoras (incluye IESS, ISSFA, ISSPOL y seguros privados)
- Planes de cobertura por aseguradora
- Tarifarios especiales por aseguradora/plan

### 4.2 Polizas de Pacientes

- Registro de poliza por paciente
- Verificacion de cobertura vigente
- Tipo de relacion: titular, conyuge, hijo, dependiente

### 4.3 Preautorizaciones

- Solicitud de preautorizacion a la aseguradora
- Seguimiento de estado: pendiente → aprobada → denegada
- Monto aprobado vs solicitado

### 4.4 Reclamos

- Generacion de reclamos a aseguradoras
- Seguimiento: borrador → enviado → en revision → aprobado → pagado
- Liquidacion y conciliacion de pagos

---

## 5. Fases de Implementacion

### Fase 1: Configuracion Inicial (Semana 1-2)

- Instalacion del sistema en servidor del cliente
- Configuracion de base de datos y seguridad
- Creacion de usuarios y roles
- Configuracion de sedes
- Carga del catalogo de pruebas con precios

**Entregable:** Sistema instalado y accesible

### Fase 2: LIS Core (Semana 3-6)

- Configuracion de analitos y rangos de referencia
- Capacitacion al personal de recepcion (registro de pacientes y muestras)
- Capacitacion a laboratoristas (ingreso de resultados, validacion)
- Capacitacion a bioquimicos (validacion medica, control de calidad)
- Pruebas con datos reales en paralelo

**Entregable:** LIS operativo con flujo manual de resultados

### Fase 3: Interfacing con Analizadores (Semana 7-10)

- Relevamiento de equipos del laboratorio (marcas, modelos, protocolos)
- Configuracion de interfaz HL7 por equipo
- Pruebas de comunicacion bidireccional
- Validacion de resultados automaticos vs manuales

**Entregable:** Analizadores conectados y transmitiendo resultados

*Nota: La duracion de esta fase depende de la cantidad y tipo de analizadores.*

### Fase 4: Facturacion y SRI (Semana 7-10)

*Se ejecuta en paralelo con Fase 3*

- Configuracion de datos fiscales (RUC, razon social, direccion)
- Instalacion de firma electronica (.p12)
- Configuracion de punto de emision
- Pruebas en ambiente SRI de pruebas
- Capacitacion a recepcion/caja
- Migracion a ambiente SRI de produccion

**Entregable:** Facturacion electronica operativa

### Fase 5: Seguros y Convenios (Semana 11-12)

- Configuracion de aseguradoras
- Carga de tarifarios por aseguradora
- Configuracion de planes y coberturas
- Capacitacion en preautorizaciones y reclamos

**Entregable:** Modulo de seguros operativo

### Fase 6: Go-Live y Acompañamiento (Semana 13-14)

- Migracion de datos historicos (si aplica)
- Operacion en produccion con acompañamiento
- Ajustes finos y resolucion de incidencias
- Documentacion de procesos del cliente

**Entregable:** Sistema en produccion estable

---

## 6. Requisitos Tecnicos

### Servidor

| Componente | Minimo | Recomendado |
|-----------|--------|-------------|
| CPU | 2 cores | 4 cores |
| RAM | 4 GB | 8 GB |
| Disco | 50 GB SSD | 100 GB SSD |
| SO | Ubuntu 22.04+ / Debian 12+ | Ubuntu 24.04 LTS |
| Red | Conexion a internet estable | Enlace dedicado |

*El sistema puede ejecutarse en servidor local (on-premise) o en la nube (AWS, Azure, DigitalOcean).*

### Estaciones de Trabajo

- Navegador web moderno (Chrome, Firefox, Edge)
- No requiere instalacion de software adicional
- Funciona en PC, laptop, tablet

### Equipamiento Adicional

- Impresora de etiquetas de codigo de barras (para muestras)
- Impresora laser o termica (para resultados y facturas)
- Firma electronica .p12 emitida por entidad autorizada en Ecuador (Security Data, ANF, etc.)

---

## 7. Seguridad

- **Autenticacion** — Usuario y contraseña con hash bcrypt
- **Autorizacion** — 5 roles con permisos diferenciados
- **Auditoria** — Registro de todas las acciones con usuario, fecha y hora
- **Comunicacion** — HTTPS/TLS para todas las conexiones
- **Datos** — Base de datos con acceso restringido, respaldos automaticos
- **Cumplimiento** — Alineado con requerimientos de la Ley Organica de Proteccion de Datos (LOPDP)

### Roles del Sistema

| Rol | Acceso |
|-----|--------|
| **Administrador** | Todo el sistema, configuracion, usuarios, reportes |
| **Recepcion** | Pacientes, caja, muestras |
| **Laboratorista** | Muestras, resultados, validacion tecnica |
| **Bioquimico** | Resultados, validacion medica, control de calidad |
| **Contador** | Facturacion, CxC, reportes financieros |

---

## 8. Soporte y Mantenimiento

### Incluido

- Soporte tecnico por correo y WhatsApp en horario laboral
- Actualizaciones del sistema (correcciones y mejoras)
- Respaldos automaticos diarios
- Monitoreo de disponibilidad del servidor

### Opcional

- Soporte 24/7 para ambientes de produccion criticos
- Capacitacion adicional para nuevo personal
- Desarrollo de reportes personalizados
- Integracion con sistemas externos (HIS, ERP)

---

## 9. Roadmap del Producto

Dimed-LIS es parte de un ecosistema modular de productos de salud:

| Producto | Descripcion | Disponibilidad |
|----------|-------------|----------------|
| **Dimed-LIS** | Laboratorio + Facturacion | Disponible |
| **Dimed-HIS** | Sistema de Informacion Hospitalaria | En desarrollo |
| **Dimed-RIS** | Sistema de Informacion Radiologica | En desarrollo |
| **Dimed-PACS** | Archivo de Imagenes Medicas | En desarrollo |
| **Dimed-ERP** | Gestion Hospitalaria (inventario, RRHH, nomina) | En desarrollo |

Cada producto funciona de forma independiente y puede integrarse con los demas mediante APIs estandar (REST, HL7, FHIR).

Si en el futuro su laboratorio se expande a servicios de imagenologia, consulta externa, o requiere gestion de inventario de reactivos, los modulos adicionales se conectan sin reemplazar lo existente.

---

## 10. Sobre Dimed

Dimed Healthcare Technology desarrolla soluciones de tecnologia para instituciones de salud en Latinoamerica. Nuestro enfoque es crear herramientas especializadas, modernas y accesibles que permitan a las instituciones de salud operar de forma eficiente y cumplir con los requerimientos regulatorios de cada pais.

---

*Para mas informacion o para agendar una demostracion, contactenos:*
*info@dimedhealthcare.com*
