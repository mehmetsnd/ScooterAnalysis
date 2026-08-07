"""Repository arayüzü (Protocol) — DIP. Core DB'ye değil bu arayüze bağlıdır; kaynak
değişse core değişmez. `analysis_timeline` sürüşleri STREAM eder, iki senaryolu hesap
saf core fonksiyonları yeniden kullanılarak Python'da yapılır (SQL'de tekrarlanmaz).

Protocol'ler `runtime_checkable`: `PostgresRideRepository`'nin sözleşmeye uyduğu
`test_backend_hardening.py`'de KONTROL EDİLİR. Aksi hâlde arayüz ile
implementasyon sessizce ayrışır (bir metot yeniden adlandırılır, Protocol eski adı
belgelemeye devam eder) — bu dosya yalnız dokümantasyon olur.
"""

from dataclasses import dataclass
from typing import Iterable, Optional, Protocol, runtime_checkable

from binbin.config import Scope


class UnknownScopeName(ValueError):
    """Kapsam olarak verilen ülke/şehir adı DB'de çözülemedi.

    Sözleşmenin parçasıdır (yalnız bir implementasyon detayı değil): çözülemeyen
    ad `[]` id listesine, o da `ANY('{}')`'e dönüşür ve tüm pipeline 0 satırla
    "başarıyla" biter. Sessiz boş sonuç uydurulmuş bir bulgudur; bu yüzden
    `resolve_scope` bunu YUTMAZ, çağıranın görmesi için yükseltir. CLI bunu
    `SystemExit`'e çevirir (process kararı shell'in işidir, data katmanının değil).
    """


@dataclass(frozen=True)
class AnalysisScope:
    """Analiz kapsamı — id listeleri. None = hepsi (filtre yok)."""

    country_ids: Optional[list[int]] = None
    city_ids: Optional[list[int]] = None


@runtime_checkable
class RideCommandRepository(Protocol):
    """Veritabanına yazma/güncelleme yapan arayüz (CQRS - Command)."""

    def classify_all(self, scope: Optional[AnalysisScope], batch_size: int = 10000, version: str = ..., refresh: bool = False) -> dict:
        """Sınıflandırılmamış başarısız sürüşleri sınıflandırıp geri yazar.

        refresh=True: damgalar önce temizlenir, kapsamdaki tüm başarısız sürüşler
        yeniden sınıflandırılır (`assess_all` ile aynı sözleşme).
        """
        raise NotImplementedError

    def assess_all(self, scope: Optional[AnalysisScope], version: str = ..., refresh: bool = False) -> dict:
        """Başarısız sürüşleri değerlendirir ve false_fault_assessment tablosunu doldurur."""
        raise NotImplementedError


@runtime_checkable
class RideQueryRepository(Protocol):
    """Analiz katmanının ihtiyaç duyduğu okuma arayüzü (CQRS - Query).

    `analysis_timeline` iki senaryolu analizin ham girdisidir (araç/zaman sıralı,
    `city.is_test = false`, yerel saat `AT TIME ZONE country.timezone` ile). `resolve_scope`
    kapsam adlarını id'ye çözer, `ops_cost_rows` maliyet modelini okur. Tek somut
    implementasyon: PostgresRideRepository.
    """

    def resolve_scope(self, scope: Scope) -> AnalysisScope:
        """Ülke/şehir adlarını id listelerine çözer (is_test şehirler hariç).

        Raises:
            UnknownScopeName: adlardan biri çözülemezse. Kısmî eşleşme de hatadır —
                iki şehirden biri tutmazsa veri sessizce yarıya iner.
        """
        raise NotImplementedError

    def analysis_timeline(
        self,
        scope: AnalysisScope,
        candidate_bounds: Optional[tuple[float, float]] = None,
    ) -> Iterable[dict]:
        """İki senaryolu analiz için araç/zaman sıralı sürüş timeline'ı.

        `candidate_bounds` verilirse sinyal-join yalnız başarısız olabilecek
        sürüşlerde çalışır (performans). Verilmezse yavaş ama daima doğru.
        """
        raise NotImplementedError

    def ops_cost_rows(self, scope: AnalysisScope) -> list[dict]:
        """ops_cost_model satırları (boşsa []). Boşsa analiz TL raporlamaz."""
        raise NotImplementedError

    def out_of_content_counts(self, scope: AnalysisScope) -> dict:
        """Analiz dışı (out-of-content) sürüş sayıları: total + mesafe/süre kırılımı."""
        raise NotImplementedError

    def signal_discrimination_rows(self, scope: AnalysisScope) -> list[dict]:
        """Kural kitabındaki her kodun başarısız/başarılı sürüş penceresindeki sıklığı.

        `core/signal_audit.summarize_signal_discrimination` bunu lift'e çevirir.
        """
        raise NotImplementedError

    def comment_corpus_rows(self, scope: AnalysisScope) -> Iterable[dict]:
        """Metni olan sürüşler (yorum/sürüş mesajı) — kelime denetiminin korpusu.

        `core/keyword_audit.summarize_keyword_discrimination` bunu lift'e çevirir.
        """
        raise NotImplementedError

    def list_data_loads(self) -> list[dict]:
        """Yükleme denetim kaydı (`loads` komutu)."""
        raise NotImplementedError


@runtime_checkable
class RideRepository(RideQueryRepository, RideCommandRepository, Protocol):
    """CLI'ın bağlandığı TEK soyutlama (okuma + yazma).

    Komutlar somut `PostgresRideRepository`'yi değil bunu bilir; kaynağı seçen tek yer
    `cli.main._repository()` composition root'udur (DIP).
    """
