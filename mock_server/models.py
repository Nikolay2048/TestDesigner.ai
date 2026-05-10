from enum import Enum


class UserStatus(str, Enum):
    NEW = "NEW"
    VERIFIED = "VERIFIED"
    BLOCKED = "BLOCKED"
    SUSPENDED = "SUSPENDED"
    FRAUD_CHECK = "FRAUD_CHECK"


class DriverLicenseStatus(str, Enum):
    NOT_SUBMITTED = "NOT_SUBMITTED"
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class VehicleStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    RESERVED = "RESERVED"
    IN_USE = "IN_USE"
    MAINTENANCE = "MAINTENANCE"
    BLOCKED = "BLOCKED"
    OFFLINE = "OFFLINE"


class BookingStatus(str, Enum):
    CREATED = "CREATED"
    CONFIRMED = "CONFIRMED"
    STARTED = "STARTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    CLOSED = "CLOSED"
    FAILED = "FAILED"


class RentalStatus(str, Enum):
    CREATED = "CREATED"
    STARTED = "STARTED"
    FINISHED = "FINISHED"
    FAILED = "FAILED"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    PAID = "PAID"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class PaymentStatus(str, Enum):
    NEW = "NEW"
    AUTHORIZED = "AUTHORIZED"
    CAPTURED = "CAPTURED"
    PAID = "PAID"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"
    REFUNDED = "REFUNDED"


class FineStatus(str, Enum):
    CREATED = "CREATED"
    PENDING = "PENDING"
    PAID = "PAID"
    CANCELLED = "CANCELLED"


class DamageReportStatus(str, Enum):
    CREATED = "CREATED"
    UNDER_REVIEW = "UNDER_REVIEW"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


# Forbidden state transitions

FORBIDDEN_BOOKING_TRANSITIONS = {
    BookingStatus.CANCELLED: [BookingStatus.CONFIRMED, BookingStatus.STARTED],
    BookingStatus.EXPIRED: [BookingStatus.CONFIRMED, BookingStatus.STARTED],
    BookingStatus.CLOSED: [BookingStatus.CONFIRMED, BookingStatus.STARTED, BookingStatus.CANCELLED],
}

FORBIDDEN_RENTAL_TRANSITIONS = {
    RentalStatus.FINISHED: [RentalStatus.STARTED],
    RentalStatus.PAID: [RentalStatus.PAYMENT_PENDING, RentalStatus.PAYMENT_FAILED],
    RentalStatus.FAILED: [RentalStatus.PAID],
}

FORBIDDEN_PAYMENT_TRANSITIONS = {
    PaymentStatus.REFUNDED: [PaymentStatus.CAPTURED, PaymentStatus.PAID],
    PaymentStatus.CAPTURED: [PaymentStatus.NEW, PaymentStatus.AUTHORIZED],
}
