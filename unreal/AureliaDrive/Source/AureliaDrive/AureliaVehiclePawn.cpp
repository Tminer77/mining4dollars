#include "AureliaVehiclePawn.h"

#include "Camera/CameraComponent.h"
#include "Components/BoxComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "GameFramework/SpringArmComponent.h"
#include "UObject/ConstructorHelpers.h"

AAureliaVehiclePawn::AAureliaVehiclePawn()
{
	PrimaryActorTick.bCanEverTick = true;

	Collision = CreateDefaultSubobject<UBoxComponent>(TEXT("Collision"));
	Collision->InitBoxExtent(FVector(110.f, 55.f, 35.f));
	Collision->SetCollisionProfileName(TEXT("Pawn"));
	Collision->SetSimulatePhysics(false);
	RootComponent = Collision;

	Body = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("Body"));
	Body->SetupAttachment(RootComponent);
	Body->SetCollisionEnabled(ECollisionEnabled::NoCollision);
	static ConstructorHelpers::FObjectFinder<UStaticMesh> CubeMesh(TEXT("/Engine/BasicShapes/Cube.Cube"));
	if (CubeMesh.Succeeded())
	{
		Body->SetStaticMesh(CubeMesh.Object);
		Body->SetRelativeScale3D(FVector(2.2f, 1.1f, 0.55f));
		Body->SetRelativeLocation(FVector(0.f, 0.f, -10.f));
	}

	SpringArm = CreateDefaultSubobject<USpringArmComponent>(TEXT("SpringArm"));
	SpringArm->SetupAttachment(RootComponent);
	SpringArm->TargetArmLength = 620.f;
	SpringArm->SocketOffset = FVector(0.f, 0.f, 140.f);
	SpringArm->bUsePawnControlRotation = false;
	SpringArm->bEnableCameraLag = true;
	SpringArm->CameraLagSpeed = 8.f;
	SpringArm->bDoCollisionTest = false;

	ChaseCamera = CreateDefaultSubobject<UCameraComponent>(TEXT("ChaseCamera"));
	ChaseCamera->SetupAttachment(SpringArm);
	ChaseCamera->SetFieldOfView(72.f);
}

void AAureliaVehiclePawn::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);

	if (SpawnLocation.IsNearlyZero())
	{
		SpawnLocation = GetActorLocation();
		SpawnRotation = GetActorRotation();
	}

	const float TargetAccel = Acceleration * ThrottleInput - BrakeDecel * BrakeInput;
	const float HandbrakeDrag = bHandbrake ? 3.2f : 1.f;
	SpeedCms += TargetAccel * DeltaSeconds;
	SpeedCms -= SpeedCms * Drag * HandbrakeDrag * DeltaSeconds;
	SpeedCms = FMath::Clamp(SpeedCms, -MaxSpeedCms * 0.28f, MaxSpeedCms);

	const float Grip = FMath::Clamp(1.f - FMath::Abs(SpeedCms) / (MaxSpeedCms * 1.6f), 0.25f, 1.f);
	const float YawDelta = SteerInput * SteerRate * Grip * FMath::Sign(SpeedCms + (FMath::Abs(SpeedCms) < 10.f && FMath::Abs(SteerInput) > 0.f ? 1.f : 0.f)) * DeltaSeconds;
	AddActorWorldRotation(FRotator(0.f, YawDelta, 0.f));
	AddActorWorldOffset(GetActorForwardVector() * SpeedCms * DeltaSeconds, true);
}

void AAureliaVehiclePawn::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)
{
	Super::SetupPlayerInputComponent(PlayerInputComponent);
	PlayerInputComponent->BindAxis(TEXT("Throttle"), this, &AAureliaVehiclePawn::SetThrottle);
	PlayerInputComponent->BindAxis(TEXT("Steer"), this, &AAureliaVehiclePawn::SetSteer);
	PlayerInputComponent->BindAxis(TEXT("Brake"), this, &AAureliaVehiclePawn::SetBrake);
	PlayerInputComponent->BindAction(TEXT("Handbrake"), IE_Pressed, this, &AAureliaVehiclePawn::HandbrakePressed);
	PlayerInputComponent->BindAction(TEXT("Handbrake"), IE_Released, this, &AAureliaVehiclePawn::HandbrakeReleased);
	PlayerInputComponent->BindAction(TEXT("ResetVehicle"), IE_Pressed, this, &AAureliaVehiclePawn::ResetToSpawn);
}

void AAureliaVehiclePawn::SetThrottle(float Value)
{
	ThrottleInput = FMath::Clamp(Value, 0.f, 1.f);
}

void AAureliaVehiclePawn::SetSteer(float Value)
{
	SteerInput = FMath::Clamp(Value, -1.f, 1.f);
}

void AAureliaVehiclePawn::SetBrake(float Value)
{
	BrakeInput = FMath::Clamp(Value, 0.f, 1.f);
}

void AAureliaVehiclePawn::HandbrakePressed()
{
	bHandbrake = true;
}

void AAureliaVehiclePawn::HandbrakeReleased()
{
	bHandbrake = false;
}

void AAureliaVehiclePawn::ResetToSpawn()
{
	SpeedCms = 0.f;
	SetActorLocationAndRotation(SpawnLocation, SpawnRotation, false, nullptr, ETeleportType::ResetPhysics);
}

float AAureliaVehiclePawn::GetSpeedKmh() const
{
	return FMath::Abs(SpeedCms) * 0.036f;
}
