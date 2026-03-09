# Motor de Cálculo Antivertido FV

Aplicación Streamlit para calcular excedentes capados por el sistema antivertido en instalaciones fotovoltaicas.

## Rutas de cálculo

| Ruta | Datos necesarios | Precisión |
|------|-----------------|:---------:|
| **A (FusionSolar)** | Excels mensuales FusionSolar | ⭐⭐⭐ |
| **B (Anual)** | Autoconsumo anual (1 valor) | ⭐⭐ |

## Despliegue con Docker

```bash
docker build -t motor-antivertido .
docker run -p 8501:8501 motor-antivertido
```

## Requisitos

- Python 3.11+
- Ver `requirements.txt`
