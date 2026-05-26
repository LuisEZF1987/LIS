#!/usr/bin/env python3
"""
SRI Ecuador — SOAP Web Service Client.

Sends signed XML documents to SRI and retrieves authorization responses.

Environments:
  Pruebas (ambiente=1):
    Reception:     https://celcer.sri.gob.ec/comprobantes-electronicos-ws/RecepcionComprobantesOffline?wsdl
    Authorization: https://celcer.sri.gob.ec/comprobantes-electronicos-ws/AutorizacionComprobantesOffline?wsdl
  Produccion (ambiente=2):
    Reception:     https://cel.sri.gob.ec/comprobantes-electronicos-ws/RecepcionComprobantesOffline?wsdl
    Authorization: https://cel.sri.gob.ec/comprobantes-electronicos-ws/AutorizacionComprobantesOffline?wsdl

Uses zeep for SOAP, with fallback to raw requests if zeep is unavailable.
"""
import base64
import logging
import re
import time

log = logging.getLogger("sri.ws_client")

# ---------------------------------------------------------------------------
# Endpoint URLs
# ---------------------------------------------------------------------------
ENDPOINTS = {
    1: {  # Pruebas
        "recepcion": (
            "https://celcer.sri.gob.ec/comprobantes-electronicos-ws/"
            "RecepcionComprobantesOffline?wsdl"
        ),
        "autorizacion": (
            "https://celcer.sri.gob.ec/comprobantes-electronicos-ws/"
            "AutorizacionComprobantesOffline?wsdl"
        ),
    },
    2: {  # Produccion
        "recepcion": (
            "https://cel.sri.gob.ec/comprobantes-electronicos-ws/"
            "RecepcionComprobantesOffline?wsdl"
        ),
        "autorizacion": (
            "https://cel.sri.gob.ec/comprobantes-electronicos-ws/"
            "AutorizacionComprobantesOffline?wsdl"
        ),
    },
}

# Retry configuration
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2  # seconds (exponential backoff)
SOAP_TIMEOUT = 30     # seconds


class SriClient:
    """
    SRI SOAP web service client with retry logic.

    Usage:
        client = SriClient()
        result = client.enviar_comprobante(xml_signed, ambiente=1)
        auth = client.autorizar_comprobante(clave_acceso, ambiente=1)
    """

    def __init__(self, max_retries: int = MAX_RETRIES, timeout: int = SOAP_TIMEOUT):
        self.max_retries = max_retries
        self.timeout = timeout
        self._zeep_available = None
        self._zeep_clients = {}

    def _is_zeep_available(self) -> bool:
        if self._zeep_available is None:
            try:
                import zeep  # noqa: F401
                self._zeep_available = True
            except ImportError:
                self._zeep_available = False
                log.info("zeep not available, using raw requests fallback")
        return self._zeep_available

    def _get_zeep_client(self, wsdl_url: str):
        """Get or create a cached zeep client."""
        if wsdl_url not in self._zeep_clients:
            import zeep
            from zeep.transports import Transport
            from requests import Session

            session = Session()
            session.verify = True
            transport = Transport(session=session, timeout=self.timeout)
            self._zeep_clients[wsdl_url] = zeep.Client(
                wsdl=wsdl_url, transport=transport,
            )
        return self._zeep_clients[wsdl_url]

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------
    def enviar_comprobante(self, xml_firmado: str, ambiente: int) -> dict:
        """
        Send a signed XML document to SRI for reception.

        Parameters:
            xml_firmado: Signed XML string
            ambiente: 1 (pruebas) or 2 (produccion)

        Returns:
            dict with keys: estado, comprobantes, clave_acceso
        """
        if ambiente not in ENDPOINTS:
            raise ValueError(f"Ambiente invalido: {ambiente}. Use 1 o 2.")

        url = ENDPOINTS[ambiente]["recepcion"]
        xml_bytes = xml_firmado.encode("utf-8")

        # Extract clave_acceso from XML
        clave_acceso = _extract_clave_acceso(xml_firmado)

        for attempt in range(1, self.max_retries + 1):
            try:
                log.info(
                    "Enviando comprobante al SRI (intento %d/%d, ambiente=%d)",
                    attempt, self.max_retries, ambiente,
                )
                if self._is_zeep_available():
                    result = self._enviar_zeep(url, xml_bytes)
                else:
                    result = self._enviar_requests(url, xml_bytes)

                result["clave_acceso"] = clave_acceso
                log.info("SRI recepcion: estado=%s", result.get("estado"))
                return result

            except Exception as e:
                log.warning(
                    "Error en envio SRI (intento %d/%d): %s",
                    attempt, self.max_retries, e,
                )
                if attempt < self.max_retries:
                    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    log.info("Reintentando en %d segundos...", delay)
                    time.sleep(delay)
                else:
                    return {
                        "estado": "ERROR",
                        "mensaje": str(e),
                        "comprobantes": [],
                        "clave_acceso": clave_acceso,
                    }

    def autorizar_comprobante(self, clave_acceso: str, ambiente: int) -> dict:
        """
        Query SRI for document authorization status.

        Parameters:
            clave_acceso: 49-digit access key
            ambiente: 1 (pruebas) or 2 (produccion)

        Returns:
            dict with keys: estado, autorizaciones
        """
        if ambiente not in ENDPOINTS:
            raise ValueError(f"Ambiente invalido: {ambiente}. Use 1 o 2.")

        url = ENDPOINTS[ambiente]["autorizacion"]

        for attempt in range(1, self.max_retries + 1):
            try:
                log.info(
                    "Consultando autorizacion SRI (intento %d/%d, clave=%s...)",
                    attempt, self.max_retries, clave_acceso[:20],
                )
                if self._is_zeep_available():
                    result = self._autorizar_zeep(url, clave_acceso)
                else:
                    result = self._autorizar_requests(url, clave_acceso)

                log.info("SRI autorizacion: estado=%s", result.get("estado"))
                return result

            except Exception as e:
                log.warning(
                    "Error en consulta autorizacion SRI (intento %d/%d): %s",
                    attempt, self.max_retries, e,
                )
                if attempt < self.max_retries:
                    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    log.info("Reintentando en %d segundos...", delay)
                    time.sleep(delay)
                else:
                    return {
                        "estado": "ERROR",
                        "mensaje": str(e),
                        "autorizaciones": [],
                    }

    # -------------------------------------------------------------------
    # Zeep implementation
    # -------------------------------------------------------------------
    def _enviar_zeep(self, wsdl_url: str, xml_bytes: bytes) -> dict:
        """Send document using zeep SOAP client."""
        client = self._get_zeep_client(wsdl_url)
        response = client.service.validarComprobante(xml_bytes)

        estado = str(getattr(response, "estado", "DESCONOCIDO"))
        comprobantes = []

        if hasattr(response, "comprobantes") and response.comprobantes:
            comp_list = response.comprobantes.comprobante
            if not isinstance(comp_list, list):
                comp_list = [comp_list]
            for comp in comp_list:
                comp_info = {
                    "claveAcceso": str(getattr(comp, "claveAcceso", "")),
                    "mensajes": _extract_zeep_messages(comp),
                }
                comprobantes.append(comp_info)

        return {"estado": estado, "comprobantes": comprobantes}

    def _autorizar_zeep(self, wsdl_url: str, clave_acceso: str) -> dict:
        """Query authorization using zeep SOAP client."""
        client = self._get_zeep_client(wsdl_url)
        response = client.service.autorizacionComprobante(clave_acceso)

        autorizaciones = []
        if hasattr(response, "autorizaciones") and response.autorizaciones:
            auth_list = response.autorizaciones.autorizacion
            if not isinstance(auth_list, list):
                auth_list = [auth_list]
            for auth in auth_list:
                autorizaciones.append({
                    "estado": str(getattr(auth, "estado", "")),
                    "numero_autorizacion": str(
                        getattr(auth, "numeroAutorizacion", "")
                    ),
                    "fecha_autorizacion": str(
                        getattr(auth, "fechaAutorizacion", "")
                    ),
                    "comprobante": str(getattr(auth, "comprobante", "")),
                    "mensajes": _extract_zeep_messages(auth),
                })

        estado = (
            autorizaciones[0]["estado"] if autorizaciones else "NO_ENCONTRADO"
        )
        return {"estado": estado, "autorizaciones": autorizaciones}

    # -------------------------------------------------------------------
    # Raw requests fallback (no zeep)
    # -------------------------------------------------------------------
    def _enviar_requests(self, wsdl_url: str, xml_bytes: bytes) -> dict:
        """Send document using raw SOAP XML via requests library."""
        import requests

        base_url = wsdl_url.replace("?wsdl", "")
        xml_b64 = base64.b64encode(xml_bytes).decode("ascii")

        soap_envelope = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<soapenv:Envelope'
            ' xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"'
            ' xmlns:ec="http://ec.gob.sri.ws.recepcion">'
            '<soapenv:Header/>'
            '<soapenv:Body>'
            '<ec:validarComprobante>'
            f'<xml>{xml_b64}</xml>'
            '</ec:validarComprobante>'
            '</soapenv:Body>'
            '</soapenv:Envelope>'
        )

        resp = requests.post(
            base_url,
            data=soap_envelope.encode("utf-8"),
            headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": ""},
            timeout=self.timeout,
            verify=True,
        )
        resp.raise_for_status()
        return _parse_recepcion_response(resp.text)

    def _autorizar_requests(self, wsdl_url: str, clave_acceso: str) -> dict:
        """Query authorization using raw SOAP XML via requests library."""
        import requests

        base_url = wsdl_url.replace("?wsdl", "")

        soap_envelope = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<soapenv:Envelope'
            ' xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"'
            ' xmlns:ec="http://ec.gob.sri.ws.autorizacion">'
            '<soapenv:Header/>'
            '<soapenv:Body>'
            '<ec:autorizacionComprobante>'
            f'<claveAccesoComprobante>{clave_acceso}</claveAccesoComprobante>'
            '</ec:autorizacionComprobante>'
            '</soapenv:Body>'
            '</soapenv:Envelope>'
        )

        resp = requests.post(
            base_url,
            data=soap_envelope.encode("utf-8"),
            headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": ""},
            timeout=self.timeout,
            verify=True,
        )
        resp.raise_for_status()
        return _parse_autorizacion_response(resp.text)


# ---------------------------------------------------------------------------
# Module-level response parsers
# ---------------------------------------------------------------------------
def _parse_recepcion_response(xml_text: str) -> dict:
    """Parse SOAP response from recepcion endpoint."""
    try:
        from lxml import etree

        root = etree.fromstring(xml_text.encode("utf-8"))
        # Strip namespaces for easier parsing
        for el in root.iter():
            tag = el.tag
            if isinstance(tag, str) and "}" in tag:
                el.tag = tag.split("}", 1)[1]

        estado_el = root.find(".//estado")
        estado = estado_el.text if estado_el is not None else "DESCONOCIDO"

        comprobantes = []
        for comp in root.findall(".//comprobante"):
            clave_el = comp.find("claveAcceso")
            comp_info = {
                "claveAcceso": clave_el.text if clave_el is not None else "",
                "mensajes": [],
            }
            for msg in comp.findall(".//mensaje"):
                comp_info["mensajes"].append({
                    "identificador": _el_text(msg, "identificador"),
                    "mensaje": _el_text(msg, "mensaje"),
                    "informacionAdicional": _el_text(msg, "informacionAdicional"),
                    "tipo": _el_text(msg, "tipo"),
                })
            comprobantes.append(comp_info)

        return {"estado": estado, "comprobantes": comprobantes}
    except ImportError:
        # lxml not available — simple string parsing
        estado = "DESCONOCIDO"
        if "RECIBIDA" in xml_text:
            estado = "RECIBIDA"
        elif "DEVUELTA" in xml_text:
            estado = "DEVUELTA"
        return {"estado": estado, "comprobantes": [], "raw_response": xml_text[:1000]}
    except Exception as e:
        log.warning("Failed to parse recepcion response: %s", e)
        return {"estado": "ERROR_PARSE", "comprobantes": [], "raw_response": xml_text[:500]}


def _parse_autorizacion_response(xml_text: str) -> dict:
    """Parse SOAP response from autorizacion endpoint."""
    try:
        from lxml import etree

        root = etree.fromstring(xml_text.encode("utf-8"))
        for el in root.iter():
            tag = el.tag
            if isinstance(tag, str) and "}" in tag:
                el.tag = tag.split("}", 1)[1]

        autorizaciones = []
        for auth in root.findall(".//autorizacion"):
            autorizaciones.append({
                "estado": _el_text(auth, "estado"),
                "numero_autorizacion": _el_text(auth, "numeroAutorizacion"),
                "fecha_autorizacion": _el_text(auth, "fechaAutorizacion"),
                "comprobante": _el_text(auth, "comprobante"),
                "mensajes": [],
            })

        estado = (
            autorizaciones[0]["estado"] if autorizaciones else "NO_ENCONTRADO"
        )
        return {"estado": estado, "autorizaciones": autorizaciones}
    except ImportError:
        estado = "DESCONOCIDO"
        if "AUTORIZADO" in xml_text:
            estado = "AUTORIZADO"
        elif "NO AUTORIZADO" in xml_text:
            estado = "NO AUTORIZADO"
        return {"estado": estado, "autorizaciones": [], "raw_response": xml_text[:1000]}
    except Exception as e:
        log.warning("Failed to parse autorizacion response: %s", e)
        return {"estado": "ERROR_PARSE", "autorizaciones": [], "raw_response": xml_text[:500]}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _el_text(parent, tag: str) -> str:
    """Get text of child element or empty string."""
    el = parent.find(tag)
    return (el.text or "") if el is not None else ""


def _extract_zeep_messages(obj) -> list:
    """Extract messages from a zeep SOAP object."""
    messages = []
    if hasattr(obj, "mensajes") and obj.mensajes:
        msgs = obj.mensajes.mensaje
        if not isinstance(msgs, list):
            msgs = [msgs]
        for msg in msgs:
            messages.append({
                "identificador": str(getattr(msg, "identificador", "")),
                "mensaje": str(getattr(msg, "mensaje", "")),
                "informacionAdicional": str(
                    getattr(msg, "informacionAdicional", "")
                ),
                "tipo": str(getattr(msg, "tipo", "")),
            })
    return messages


def _extract_clave_acceso(xml_str: str) -> str:
    """Extract claveAcceso from XML string."""
    try:
        from lxml import etree
        root = etree.fromstring(xml_str.encode("utf-8"))
        el = root.find(".//{*}claveAcceso")
        if el is None:
            el = root.find(".//claveAcceso")
        return el.text if el is not None else ""
    except Exception:
        match = re.search(r"<claveAcceso>(\d{49})</claveAcceso>", xml_str)
        return match.group(1) if match else ""


# ---------------------------------------------------------------------------
# Convenience functions (module-level)
# ---------------------------------------------------------------------------
def enviar_comprobante(xml_firmado: str, ambiente: int) -> dict:
    """Module-level convenience wrapper."""
    client = SriClient()
    return client.enviar_comprobante(xml_firmado, ambiente)


def autorizar_comprobante(clave_acceso: str, ambiente: int) -> dict:
    """Module-level convenience wrapper."""
    client = SriClient()
    return client.autorizar_comprobante(clave_acceso, ambiente)
