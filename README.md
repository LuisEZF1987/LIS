# Dimed-LIS

Sistema de Informacion de Laboratorio (LIS) con facturacion electronica SRI para Ecuador.

## Modulos

- **LIS** - Gestion de muestras, resultados, analitos, control de calidad, interfacing con analizadores
- **Facturacion** - Facturas, cuentas por cobrar, pagos, facturacion electronica SRI
- **Seguros** - Planes, polizas, preautorizaciones, reclamos, tarifarios

## Inicio Rapido

```bash
cp .env.example .env
# Editar .env con sus credenciales

make up       # Iniciar servicios
make migrate  # Crear tablas
make seed     # Datos demo (opcional)
```

Acceder a http://localhost:9000

## Requisitos

- Docker y Docker Compose
- Firma electronica (.p12) del SRI para facturacion

## Estructura

```
lis/       - Modulo LIS (puerto 9008)
billing/   - Modulo Facturacion + SRI (puerto 9009)
web/       - Dashboard unificado (puerto 9000)
migrations/- Esquemas de base de datos
scripts/   - Utilidades (setup, migracion, seed)
docs/      - Documentacion y propuestas
```

## Parte del Ecosistema Dimed

Este producto es parte de la familia de productos Dimed:

| Producto | Descripcion | Estado |
|----------|-------------|--------|
| **Dimed-LIS** | Laboratorio + Facturacion | En desarrollo |
| Dimed-HIS | Gestion Hospitalaria | Planificado |
| Dimed-RIS | Informes Radiologicos | Planificado |
| Dimed-PACS | Archivo de Imagenes | Planificado |
| Dimed-ERP | Hospitalizacion, Inventario, RRHH | Planificado |

Cada producto funciona de forma independiente y se conecta con los demas via API REST.
